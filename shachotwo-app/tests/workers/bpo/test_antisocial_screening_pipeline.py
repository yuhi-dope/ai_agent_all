"""反社チェックパイプライン テスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from workers.bpo.common.pipelines.antisocial_screening_pipeline import (
    ANTISOCIAL_KEYWORDS,
    RISK_SCORE_CAUTION,
    RISK_SCORE_DANGER,
    SIMILARITY_THRESHOLD,
    AntisocialScreeningPipelineResult,
    _calc_risk_score,
    _check_keyword_match,
    _levenshtein_similarity,
    run_antisocial_screening_pipeline,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = str(uuid4())


# ─── モックファクトリ ────────────────────────────────────────────────────────

def _mock_rule_matcher_out(matched_rules: list | None = None) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "matched_rules": matched_rules or [],
            "applied_values": {},
            "unmatched_fields": [],
        },
        confidence=0.9,
        cost_yen=0.0,
        duration_ms=5,
    )


def _mock_extractor_out(risk_score: float = 0.0, risk_level: str = "safe") -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="structured_extractor",
        success=True,
        result={
            "risk_score": risk_score,
            "risk_level": risk_level,
            "summary": f"スクリーニング完了。リスクレベル: {risk_level}",
            "recommended_action": (
                "取引停止推奨" if risk_level == "danger"
                else "要確認" if risk_level == "caution"
                else "取引継続可"
            ),
        },
        confidence=0.9,
        cost_yen=0.01,
        duration_ms=200,
    )


def _mock_generator_out() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"document": "## 反社チェックレポート\nテスト結果"},
        confidence=0.95,
        cost_yen=0.01,
        duration_ms=300,
    )


def _mock_validator_out() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="output_validator",
        success=True,
        result={"valid": True, "missing": [], "empty": [], "type_errors": [], "warnings": []},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=5,
    )


def _make_db_mock(blacklist_entries: list | None = None, vendor_data: list | None = None) -> MagicMock:
    """
    Supabase DBのモックを生成する。

    パイプライン内では get_service_client() が複数回呼ばれる:
      - Step1 1回目: target_id がある場合の取引先取得 (.eq().eq().limit().execute())
      - Step1 2回目: ブラックリスト取得 (.eq().eq().contains().execute())
      - Step6: bpo_approvals insert
      - Step6: execution_logs insert
    contains() の有無でブラックリスト取得を識別する。
    """
    db = MagicMock()

    bl_data = blacklist_entries if blacklist_entries is not None else []
    vendor_data_list = vendor_data if vendor_data is not None else []

    vendor_resp = MagicMock()
    vendor_resp.data = vendor_data_list

    bl_resp = MagicMock()
    bl_resp.data = bl_data

    # contains() が呼ばれた時点でブラックリスト用チェーンを返す
    contains_chain = MagicMock()
    contains_chain.execute.return_value = bl_resp

    # eq チェーン: .eq().eq().eq()... を何度でも返す
    eq_chain = MagicMock()
    eq_chain.eq.return_value = eq_chain
    eq_chain.select.return_value = eq_chain
    eq_chain.limit.return_value = eq_chain
    eq_chain.order.return_value = eq_chain
    eq_chain.execute.return_value = vendor_resp
    eq_chain.contains.return_value = contains_chain

    table_mock = MagicMock()
    table_mock.select.return_value = eq_chain
    table_mock.insert.return_value.execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

    db.table.return_value = table_mock
    return db


# ─── ユーティリティ関数のユニットテスト ────────────────────────────────────

class TestLevenshteinSimilarity:
    def test_identical_strings(self):
        """完全一致で類似度1.0。"""
        assert _levenshtein_similarity("山口組", "山口組") == 1.0

    def test_empty_strings(self):
        """両方空で1.0。"""
        assert _levenshtein_similarity("", "") == 1.0

    def test_one_empty(self):
        """片方空で0.0。"""
        assert _levenshtein_similarity("山口組", "") == 0.0
        assert _levenshtein_similarity("", "山口組") == 0.0

    def test_similar_strings(self):
        """類似文字列で中程度のスコア。"""
        sim = _levenshtein_similarity("山口組", "山ロ組")
        assert 0.0 < sim < 1.0

    def test_completely_different(self):
        """全く異なる文字列で低スコア。"""
        sim = _levenshtein_similarity("株式会社山田商事", "XXXXXXXX")
        assert sim < 0.5


class TestCheckKeywordMatch:
    def test_antisocial_keyword_hit(self):
        """反社キーワードが含まれる場合にマッチ。"""
        flags = _check_keyword_match("関東山口組フロント")
        assert len(flags) > 0
        assert any("山口組" in f for f in flags)

    def test_pattern_hit_short_kanji_with_kumi(self):
        """短い漢字+組のパターンにマッチ。"""
        flags = _check_keyword_match("山組")
        assert any("パターン一致" in f for f in flags)

    def test_kougyou_keyword(self):
        """興業キーワードにマッチ。"""
        flags = _check_keyword_match("東京興業")
        assert any("興業" in f or "パターン一致" in f for f in flags)

    def test_clean_company_name(self):
        """一般的な社名にはマッチしない。"""
        flags = _check_keyword_match("株式会社田中製作所")
        assert len(flags) == 0

    def test_empty_text(self):
        """空文字列は空リストを返す。"""
        assert _check_keyword_match("") == []


class TestCalcRiskScore:
    def test_no_matches_returns_safe(self):
        """マッチなしでsafe/0.0。"""
        score, level = _calc_risk_score([], [])
        assert score == 0.0
        assert level == "safe"

    def test_perfect_blacklist_match_returns_danger(self):
        """完全一致（similarity=1.0）でdanger/1.0。"""
        matches = [{"similarity": 1.0, "check_name": "山口組", "blacklist_name": "山口組"}]
        score, level = _calc_risk_score(matches, [])
        assert score == 1.0
        assert level == "danger"

    def test_high_similarity_returns_caution_or_danger(self):
        """高類似度（SIMILARITY_THRESHOLD以上）でcautionまたはdanger。"""
        matches = [{"similarity": SIMILARITY_THRESHOLD, "check_name": "山口組", "blacklist_name": "山口绀"}]
        score, level = _calc_risk_score(matches, [])
        assert score >= RISK_SCORE_CAUTION
        assert level in ("caution", "danger")

    def test_keyword_flags_return_caution(self):
        """キーワードフラグのみでcaution相当のスコア。"""
        flags = ["キーワード一致: 興業"]
        score, level = _calc_risk_score([], flags)
        assert score >= RISK_SCORE_CAUTION


# ─── パイプライン統合テスト ─────────────────────────────────────────────────

class TestAntisocialScreeningClean:
    """クリーンな取引先 → safe判定"""

    @pytest.mark.asyncio
    async def test_clean_vendor_returns_safe(self):
        """ブラックリスト・キーワード一致なし → risk_level=safe。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "株式会社田中製作所",
            "representative": "田中太郎",
            "address": "東京都渋谷区1-1-1",
            "phone": "03-1234-5678",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.0, "safe")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert result.risk_level == "safe"
        assert result.risk_score == 0.0
        assert len(result.matched_flags) == 0
        assert result.approval_required is True  # 反社チェックは常に承認必須


class TestAntisocialScreeningBlacklistHit:
    """ブラックリスト完全一致 → danger判定"""

    @pytest.mark.asyncio
    async def test_blacklist_perfect_match_returns_danger(self):
        """ブラックリストと完全一致する社名でdanger。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "山口組フロント商事",
            "representative": "山田一郎",
        }
        blacklist = [
            {
                "id": str(uuid4()),
                "title": "山口組フロント商事",
                "content": "指定暴力団フロント企業",
                "metadata": {"type": "antisocial_blacklist", "group": "山口組"},
                "tags": ["暴力団", "指定暴力団"],
            }
        ]
        db_mock = _make_db_mock(blacklist_entries=blacklist)

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(1.0, "danger")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert result.risk_level == "danger"
        assert result.risk_score == 1.0
        # ブラックリスト一致フラグが含まれている
        assert any("ブラックリスト" in flag for flag in result.matched_flags)
        assert result.approval_required is True


class TestAntisocialScreeningKeywordHit:
    """反社キーワードを含む社名 → caution以上の判定"""

    @pytest.mark.asyncio
    async def test_keyword_in_company_name_returns_caution_or_danger(self):
        """社名に反社キーワード（興業）を含む場合にcaution以上。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "関東興業株式会社",
            "address": "東京都台東区2-2-2",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.5, "caution")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert result.risk_level in ("caution", "danger")
        # キーワードフラグが記録されている
        assert any("興業" in flag or "パターン一致" in flag for flag in result.matched_flags)


class TestAntisocialScreeningAllSteps:
    """全6ステップが実行されることを確認"""

    @pytest.mark.asyncio
    async def test_all_6_steps_executed(self):
        """正常系で全6ステップがstepsリストに含まれる。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "株式会社テスト商事",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.0, "safe")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert len(result.steps) == 6

        step_names = [s.step_name for s in result.steps]
        assert step_names == [
            "db_reader",
            "rule_matcher",
            "rule_matcher",
            "extractor",
            "generator",
            "validator",
        ]
        step_nos = [s.step_no for s in result.steps]
        assert step_nos == [1, 2, 3, 4, 5, 6]


class TestAntisocialScreeningApprovalAlwaysRequired:
    """承認フラグは常にTrueであること"""

    @pytest.mark.asyncio
    async def test_approval_required_always_true(self):
        """クリーンな取引先でもapproval_required=True。"""
        input_data = {
            "target_type": "company",
            "target_name": "株式会社安全商会",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.0, "safe")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.approval_required is True
        assert result.final_output["approval_required"] is True


class TestAntisocialScreeningReportGenerated:
    """レポートが生成されることを確認"""

    @pytest.mark.asyncio
    async def test_report_generated_in_final_output(self):
        """final_output に report が含まれ、report_generated=True。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "テスト商事",
            "representative": "代表者名",
            "address": "大阪府大阪市1-1",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.0, "safe")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.report_generated is True
        assert "report" in result.final_output
        assert len(result.final_output["report"]) > 0


class TestAntisocialScreeningDbFailFallback:
    """DB接続失敗時にStep1でfailedを返すこと"""

    @pytest.mark.asyncio
    async def test_db_failure_returns_failed_step(self):
        """get_service_clientが例外を投げた場合にfailed_step=db_reader。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "テスト商事",
        }

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            side_effect=Exception("DB接続エラー"),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is False
        assert result.failed_step == "db_reader"


class TestAntisocialScreeningScreeningTarget:
    """screening_targetフィールドが正しく設定されること"""

    @pytest.mark.asyncio
    async def test_screening_target_set_from_target_name(self):
        """target_nameがscreening_targetに設定される。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "株式会社ターゲット",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.0, "safe")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.screening_target == "株式会社ターゲット"


class TestAntisocialScreeningRiskScoreRange:
    """risk_scoreが0.0〜1.0の範囲内であること"""

    @pytest.mark.asyncio
    async def test_risk_score_in_valid_range(self):
        """risk_scoreは常に0.0〜1.0の範囲内。"""
        input_data = {
            "target_type": "vendor",
            "target_name": "株式会社テスト",
        }
        db_mock = _make_db_mock(blacklist_entries=[])

        with patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.get_service_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=_mock_rule_matcher_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=_mock_extractor_out(0.3, "caution")),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ), patch(
            "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_antisocial_screening_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert 0.0 <= result.risk_score <= 1.0
        assert result.risk_level in ("safe", "caution", "danger")

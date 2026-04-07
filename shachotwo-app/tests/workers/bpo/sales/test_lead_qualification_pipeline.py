"""SFA パイプライン① リードクオリフィケーション テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.sfa.lead_qualification_pipeline import (
    SUPPORTED_INDUSTRIES,
    LeadQualificationResult,
    _calculate_score,
    _determine_routing,
    _normalize_industry,
    run_lead_qualification_pipeline,
)
from workers.micro.models import MicroAgentOutput

# scoring_model_versions を参照するDBコールをモックするユーティリティ
# ボーナスなし（空の data）を返すモック Supabase クライアント
def _mock_db_no_bonus():
    """scoring_model_versions に active なモデルがない状態のモック"""
    mock_result = MagicMock()
    mock_result.data = []
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = mock_result
    mock_db = MagicMock()
    mock_db.table.return_value = mock_table
    return mock_db

# ─── テスト用フィクスチャ ─────────────────────────────────────────────────────

COMPANY_ID = "test-company-001"

# 最高スコアになるインプット（業種マッチ+従業員最適+即導入+BPOコア+紹介）
HIGH_SCORE_INPUT = {
    "company_name": "株式会社テスト建設",
    "contact_name": "山田太郎",
    "contact_email": "yamada@test-kensetsu.co.jp",
    "contact_phone": "03-1234-5678",
    "industry": "建設業",
    "employee_count": 30,
    "urgency": "すぐ導入したい",
    "budget": "BPOコア",
    "source": "紹介",
    "need": "見積作業の自動化と安全書類の作成効率化",
}

# REVIEW帯インプット（業種マッチ+従業員中規模+検討中+予算なし+Web）
REVIEW_INPUT = {
    "company_name": "有限会社サンプル製造",
    "contact_name": "鈴木花子",
    "contact_email": "suzuki@sample-mfg.co.jp",
    "industry": "製造業",
    "employee_count": 80,
    "urgency": "検討中",
    "budget": "",
    "source": "Web",
    "need": "生産管理を効率化したい",
}

# NURTURINGインプット（業種外+少人数+情報収集+予算なし）
NURTURING_INPUT = {
    "company_name": "個人事務所ABC",
    "contact_name": "田中次郎",
    "contact_email": "tanaka@abc-office.jp",
    "industry": "コンサルティング",  # 対応業種外
    "employee_count": 3,
    "urgency": "情報収集",
    "budget": "",
    "source": "Google",
    "need": "業務効率化について知りたい",
}


# ─── スコアリングロジック単体テスト ──────────────────────────────────────────

class TestCalculateScore:
    """_calculate_score() の各加点ルールを独立して検証する"""

    def test_max_score_without_bonus(self) -> None:
        """業種マッチ+従業員最適+即導入+BPOコア = 105pt"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "すぐ導入したい",
            "budget": "BPOコア",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        assert score == 105  # 30+25+30+20
        assert len(reasons) >= 4

    def test_referral_bonus(self) -> None:
        """紹介ボーナス +15pt が正しく加算される"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "すぐ導入したい",
            "budget": "BPOコア",
            "source": "紹介",
        }
        score, _ = _calculate_score(extracted)
        assert score == 120  # 30+25+30+20+15

    def test_event_bonus(self) -> None:
        """イベントボーナス +10pt が正しく加算される"""
        extracted = {
            "industry": "歯科医院",
            "employee_count": 20,
            "urgency": "即導入",
            "budget": "BPOコア",
            "source": "イベント",
        }
        score, _ = _calculate_score(extracted)
        assert score == 115  # 30+25+30+20+10

    def test_outside_industry(self) -> None:
        """対応業種外は +5pt のみ"""
        extracted = {
            "industry": "コンサルティング",
            "employee_count": 30,
            "urgency": "すぐ導入したい",
            "budget": "BPOコア",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        industry_reason = next(r for r in reasons if r["factor"] == "業種マッチ")
        assert industry_reason["points"] == 5
        assert not industry_reason["matched"]

    def test_employee_300_over(self) -> None:
        """300名超は従業員規模 +5pt のみ"""
        extracted = {
            "industry": "建設業",
            "employee_count": 500,
            "urgency": "情報収集",
            "budget": "",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        emp_reason = next(r for r in reasons if r["factor"] == "従業員規模")
        assert emp_reason["points"] == 5
        assert not emp_reason["matched"]

    def test_employee_51_to_300(self) -> None:
        """51-300名は +20pt"""
        extracted = {
            "industry": "製造業",
            "employee_count": 100,
            "urgency": "検討中",
            "budget": "",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        emp_reason = next(r for r in reasons if r["factor"] == "従業員規模")
        assert emp_reason["points"] == 20

    def test_brain_only_budget(self) -> None:
        """ブレインのみ予算は +10pt"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "検討中",
            "budget": "ブレインのみ",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        budget_reason = next(r for r in reasons if r["factor"] == "予算感")
        assert budget_reason["points"] == 10

    def test_no_budget_info(self) -> None:
        """予算未回答は 0pt"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "検討中",
            "budget": "",
            "source": "Web",
        }
        _, reasons = _calculate_score(extracted)
        budget_reason = next(r for r in reasons if r["factor"] == "予算感")
        assert budget_reason["points"] == 0

    def test_score_reasons_all_factors_present(self) -> None:
        """全ファクターについてreasonsが生成される"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "検討中",
            "budget": "BPOコア",
            "source": "Web",
        }
        _, reasons = _calculate_score(extracted)
        factors = {r["factor"] for r in reasons}
        assert "業種マッチ" in factors
        assert "従業員規模" in factors
        assert "ニーズ緊急度" in factors
        assert "予算感" in factors

    def test_learned_bonus_applied(self) -> None:
        """スコアが正常に計算される（ボーナス機構は廃止）"""
        extracted = {
            "industry": "建設業",
            "employee_count": 30,
            "urgency": "検討中",
            "budget": "",
            "source": "Web",
        }
        score, reasons = _calculate_score(extracted)
        # 検討中+建設業+10-50名 のベーススコアは 30+25+15+0=70
        assert score == 70
        factors = {r["factor"] for r in reasons}
        assert "業種マッチ" in factors

    def test_all_low_factors(self) -> None:
        """全てのファクターが最小値の場合"""
        extracted = {
            "industry": "コンサルティング",  # 対応業種外 +5
            "employee_count": 5,  # 10名未満 +5
            "urgency": "情報収集",  # 情報収集 +5
            "budget": "",  # 予算未回答 0
            "source": "Web",  # ボーナスなし 0
        }
        score, reasons = _calculate_score(extracted)
        # 最小スコア: 5+5+5+0=15
        assert score == 15
        # 受注パターンボーナスは付かない
        assert not any(r["factor"] == "受注パターンボーナス" for r in reasons)


class TestDetermineRouting:
    """_determine_routing() の振り分けロジックを検証する"""

    def test_qualified_at_70(self) -> None:
        assert _determine_routing(70) == "QUALIFIED"

    def test_qualified_above_70(self) -> None:
        assert _determine_routing(100) == "QUALIFIED"

    def test_review_at_40(self) -> None:
        assert _determine_routing(40) == "REVIEW"

    def test_review_at_69(self) -> None:
        assert _determine_routing(69) == "REVIEW"

    def test_nurturing_at_39(self) -> None:
        assert _determine_routing(39) == "NURTURING"

    def test_nurturing_at_0(self) -> None:
        assert _determine_routing(0) == "NURTURING"


class TestNormalizeIndustry:
    """_normalize_industry() の日本語→コード変換を検証する"""

    def test_kensetsu(self) -> None:
        assert _normalize_industry("建設業") == "construction"

    def test_manufacturing(self) -> None:
        assert _normalize_industry("製造業") == "manufacturing"

    def test_dental(self) -> None:
        assert _normalize_industry("歯科医院") == "dental"

    def test_partial_match(self) -> None:
        assert _normalize_industry("株式会社建設") == "construction"

    def test_unknown_returns_lowercased(self) -> None:
        result = _normalize_industry("コンサルティング")
        assert result == "コンサルティング"

    def test_none_returns_none(self) -> None:
        assert _normalize_industry(None) is None

    def test_supported_industries_count(self) -> None:
        """対応業種が16件あることを確認"""
        assert len(SUPPORTED_INDUSTRIES) == 16


# ─── パイプライン統合テスト ───────────────────────────────────────────────────

def _mock_extractor_output(extracted: dict) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="structured_extractor",
        success=True,
        result={"extracted": extracted, "missing_fields": []},
        confidence=0.9,
        cost_yen=0.5,
        duration_ms=100,
    )


def _mock_rule_matcher_output(applied: dict) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={"matched_rules": [], "applied_values": applied, "unmatched_fields": []},
        confidence=0.8,
        cost_yen=0.0,
        duration_ms=50,
    )


def _mock_saas_writer_output() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={"success": True, "operation_id": "mock-lead-id-123", "dry_run": True},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=30,
    )


@pytest.mark.asyncio
async def test_high_score_qualified() -> None:
    """高スコアリードがQUALIFIEDになり6ステップ完走する"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(HIGH_SCORE_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={
                "subject": "テスト件名",
                "body": "テスト本文",
                "cost_yen": 0.3,
                "is_fallback": False,
            },
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=HIGH_SCORE_INPUT,
            dry_run=True,
        )

    assert isinstance(result, LeadQualificationResult)
    assert result.success is True
    assert result.routing == "QUALIFIED"
    assert result.lead_score >= 70
    assert len(result.steps) == 6
    assert result.failed_step is None


@pytest.mark.asyncio
async def test_review_score() -> None:
    """中スコアリードがREVIEWになる"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(REVIEW_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": True},
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=REVIEW_INPUT,
            dry_run=True,
        )

    assert result.success is True
    assert result.routing == "REVIEW"
    assert 40 <= result.lead_score < 70


@pytest.mark.asyncio
async def test_nurturing_score() -> None:
    """低スコアリードがNURTURINGになる"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(NURTURING_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": True},
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=NURTURING_INPUT,
            dry_run=True,
        )

    assert result.success is True
    assert result.routing == "NURTURING"
    assert result.lead_score < 40


@pytest.mark.asyncio
async def test_raw_text_input_calls_extractor() -> None:
    """raw_textを渡した場合にrun_structured_extractorが呼ばれる"""
    mock_extracted = {
        "company_name": "株式会社OCRテスト",
        "industry": "建設業",
        "employee_count": 25,
        "urgency": "すぐ導入したい",
        "budget": "BPOコア",
        "source": "紹介",
        "need": "見積自動化",
    }
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(mock_extracted),
        ) as mock_extractor,
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(mock_extracted),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": False},
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data={"raw_text": "建設業 株式会社OCRテスト 従業員25名..."},
            dry_run=True,
        )

    mock_extractor.assert_awaited_once()
    assert result.success is True


@pytest.mark.asyncio
async def test_extractor_failure_returns_failed_step() -> None:
    """structured_extractor失敗時はfailed_step='structured_extractor'で返る"""
    with patch(
        "workers.bpo.sales.sfa.lead_qualification_pipeline.run_structured_extractor",
        new_callable=AsyncMock,
        return_value=MicroAgentOutput(
            agent_name="structured_extractor",
            success=False,
            result={"error": "LLM timeout"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=30001,
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data={"raw_text": "some form text"},
            dry_run=True,
        )

    assert result.success is False
    assert result.failed_step == "structured_extractor"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_saas_writer_failure_is_non_fatal() -> None:
    """saas_writer失敗でもパイプライン全体はsuccess=Trueで返る"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(HIGH_SCORE_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=MicroAgentOutput(
                agent_name="saas_writer",
                success=False,
                result={"error": "DB connection failed"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=100,
            ),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": True},
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=HIGH_SCORE_INPUT,
            dry_run=True,
        )

    assert result.success is True
    assert result.lead_id is None  # DB保存失敗なのでIDなし
    assert result.routing == "QUALIFIED"  # スコアリング結果は正常


@pytest.mark.asyncio
async def test_step_count_and_costs() -> None:
    """6ステップが全て記録され、コストが正しく集計される"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(HIGH_SCORE_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={
                "subject": "s", "body": "b",
                "cost_yen": 1.5,
                "is_fallback": False,
            },
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=HIGH_SCORE_INPUT,
            dry_run=True,
        )

    assert len(result.steps) == 6
    step_names = [s.step_name for s in result.steps]
    assert step_names == [
        "structured_extractor",
        "rule_matcher",
        "score_calculator",
        "routing_evaluator",
        "saas_writer",
        "message_generator",
    ]
    # コスト集計: step6のcost_yenが含まれているか確認
    assert result.total_cost_yen >= 1.5


@pytest.mark.asyncio
async def test_dry_run_does_not_write_to_db() -> None:
    """dry_run=True のとき saas_writer に dry_run=True が渡される"""
    captured_payload: dict = {}

    async def capture_saas_writer(input):
        captured_payload.update(input.payload)
        return _mock_saas_writer_output()

    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(HIGH_SCORE_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            side_effect=capture_saas_writer,
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": True},
        ),
    ):
        await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=HIGH_SCORE_INPUT,
            dry_run=True,
        )

    assert captured_payload.get("dry_run") is True


@pytest.mark.asyncio
async def test_summary_method() -> None:
    """summary() が正常に文字列を返す"""
    with (
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=_mock_rule_matcher_output(HIGH_SCORE_INPUT),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.sfa.lead_qualification_pipeline._generate_thank_you_email",
            new_callable=AsyncMock,
            return_value={"subject": "s", "body": "b", "cost_yen": 0.0, "is_fallback": False},
        ),
    ):
        result = await run_lead_qualification_pipeline(
            company_id=COMPANY_ID,
            input_data=HIGH_SCORE_INPUT,
            dry_run=True,
        )

    summary = result.summary()
    assert "リードクオリフィケーション" in summary
    assert "QUALIFIED" in summary
    assert "Step 1" in summary
    assert "Step 6" in summary

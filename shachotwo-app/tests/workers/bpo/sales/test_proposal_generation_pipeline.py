"""
SFA パイプライン② — 提案書AI生成・送付 テスト

対象: workers/bpo/sales/pipelines/proposal_generation_pipeline.py

テスト方針:
- 外部依存（Supabase / Gemini LLM / WeasyPrint / SendGrid）は全てモック
- 各ステップの成功/失敗分岐を独立してテスト
- dry_run=True を使ってメール/DB書き込みをスキップ
- CONFIDENCE_WARNING_THRESHOLD 未満の warning が記録されることを確認
"""
import json
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# WeasyPrint が CI 環境に存在しない場合のスタブ
# workers/micro/__init__.py が eager import するため、テスト前に差し込む
_weasyprint_stub = types.ModuleType("weasyprint")
_weasyprint_stub.HTML = MagicMock()
sys.modules.setdefault("weasyprint", _weasyprint_stub)

from workers.bpo.sales.pipelines.proposal_generation_pipeline import (
    ProposalGenerationResult,
    StepResult,
    _calc_pricing,
    _load_genome,
    _make_proposal_number,
    _parse_llm_json,
    run_proposal_generation_pipeline,
    CONFIDENCE_WARNING_THRESHOLD,
    INDUSTRY_LABELS,
    MODULE_PRICES,
)

COMPANY_ID = "test-company-sales-001"
LEAD_ID = "lead-uuid-0001"
OPP_ID = "opp-uuid-0001"

# ────────────────────────────────────────────────────────────
# フィクスチャ
# ────────────────────────────────────────────────────────────

MOCK_LEAD = {
    "id": LEAD_ID,
    "company_name": "テスト建設株式会社",
    "contact_name": "山田太郎",
    "contact_email": "yamada@test-construction.co.jp",
    "industry": "construction",
    "employee_count": 45,
    "score": 80,
    "score_reasons": ["業種マッチ", "即導入希望"],
    "status": "qualified",
}

MOCK_OPPORTUNITY = {
    "id": OPP_ID,
    "company_id": COMPANY_ID,
    "lead_id": LEAD_ID,
    "target_company_name": "テスト建設株式会社",
    "target_industry": "construction",
    "selected_modules": ["brain", "bpo_core"],
    "monthly_amount": 280000,
    "stage": "qualification",
}

MOCK_PROPOSAL_JSON = {
    "cover": {
        "title": "AI業務アシスタント「シャチョツー」ご提案書",
        "subtitle": "テスト建設株式会社 様向け",
        "target_company": "テスト建設株式会社",
        "date": "2026-03-21",
    },
    "pain_points": [
        {
            "category": "積算・見積作業の負担",
            "description": "見積作成に月20時間以上かかっている",
            "impact": "受注機会の損失と担当者の残業増加",
            "priority": "high",
        },
        {
            "category": "安全書類の作成負担",
            "description": "安全書類の記載に多くの時間が取られる",
            "impact": "本来業務の時間が圧迫される",
            "priority": "medium",
        },
    ],
    "solution_map": [
        {
            "pain_point": "積算・見積作業の負担",
            "solution": "AIによる自動見積生成",
            "module": "BPOコア（建設見積）",
            "effect": "見積作成時間を80%削減",
        }
    ],
    "modules": [
        {
            "name": "ブレイン",
            "description": "社長の暗黙知をAIが学習",
            "monthly_price": 30000,
            "key_features": ["Q&A", "ナレッジ蓄積"],
        },
        {
            "name": "BPOコア",
            "description": "建設業特化の業務自動化",
            "monthly_price": 250000,
            "key_features": ["見積自動生成", "安全書類作成"],
        },
    ],
    "pricing": {
        "modules_total": 280000,
        "annual_total": 3360000,
        "discount_note": None,
    },
    "roi_estimate": {
        "current_cost_monthly": 500000,
        "after_cost_monthly": 280000,
        "savings_monthly": 220000,
        "payback_months": 2,
        "calculation_basis": "月20h削減 × 2名 × 3000円/h + BPO効果",
        "confidence": 0.55,
    },
    "timeline": [
        {
            "phase": "Phase 1",
            "period": "Week 1-2",
            "tasks": ["ナレッジ入力", "初期設定"],
            "milestone": "ブレイン稼働",
        },
        {
            "phase": "Phase 2",
            "period": "Week 3-4",
            "tasks": ["BPO設定", "テスト"],
            "milestone": "見積自動化稼働",
        },
    ],
}

MOCK_PDF_BYTES = b"%PDF-1.4 mock pdf content"


def _make_saas_reader_side_effect(lead: dict, opportunity: dict):
    """run_saas_reader の呼び出し引数に応じてモック出力を返す。"""
    from workers.micro.models import MicroAgentOutput

    async def _side_effect(inp):
        table = inp.payload.get("params", {}).get("table", "")
        if table == "leads":
            return MicroAgentOutput(
                agent_name="saas_reader", success=True,
                result={"data": [lead], "count": 1, "service": "supabase", "mock": False},
                confidence=1.0, cost_yen=0.0, duration_ms=5,
            )
        elif table == "opportunities":
            return MicroAgentOutput(
                agent_name="saas_reader", success=True,
                result={"data": [opportunity], "count": 1, "service": "supabase", "mock": False},
                confidence=1.0, cost_yen=0.0, duration_ms=5,
            )
        return MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"data": [], "count": 0, "service": "supabase", "mock": True},
            confidence=0.5, cost_yen=0.0, duration_ms=2,
        )
    return _side_effect


def _make_rule_matcher_output():
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="rule_matcher", success=True,
        result={
            "industry_template": {
                "genome": {
                    "id": "construction",
                    "name": "建設業",
                    "description": "建設業向けのテンプレート",
                    "industry": "建設業",
                    "sub_industries": [],
                    "typical_employee_range": "10-300",
                    "departments": [],
                },
                "matched_rules": [],
                "applied_values": {},
                "industry_pain_defaults": [
                    "積算・見積作成に膨大な時間がかかる",
                    "安全書類・施工計画書の作成負担",
                    "下請管理・工程管理の属人化",
                    "熟練技術者の退職によるノウハウ喪失",
                ],
            },
            "industry": "construction",
        },
        confidence=0.65, cost_yen=0.0, duration_ms=10,
    )


def _make_pdf_output(success: bool = True):
    from workers.micro.models import MicroAgentOutput
    if success:
        return MicroAgentOutput(
            agent_name="pdf_generator", success=True,
            result={"pdf_bytes": MOCK_PDF_BYTES, "size_kb": 24.5, "template_name": "proposal_template.html"},
            confidence=1.0, cost_yen=0.0, duration_ms=200,
        )
    return MicroAgentOutput(
        agent_name="pdf_generator", success=False,
        result={"error": "WeasyPrint failed"},
        confidence=0.0, cost_yen=0.0, duration_ms=50,
    )


def _make_saas_writer_output(operation_id: str = "op-001"):
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="saas_writer", success=True,
        result={"success": True, "operation_id": operation_id, "dry_run": False},
        confidence=1.0, cost_yen=0.0, duration_ms=30,
    )


# ────────────────────────────────────────────────────────────
# ユーティリティ関数テスト
# ────────────────────────────────────────────────────────────

class TestCalcPricing:
    def test_brain_only(self):
        result = _calc_pricing(["brain"])
        assert result["monthly_total"] == 30_000
        assert result["tax"] == 3_000
        assert result["total_with_tax"] == 33_000
        assert result["annual_total"] == 360_000

    def test_brain_and_bpo_core(self):
        result = _calc_pricing(["brain", "bpo_core"])
        assert result["monthly_total"] == 280_000
        assert result["tax"] == 28_000
        assert result["total_with_tax"] == 308_000

    def test_additional_module(self):
        result = _calc_pricing(["brain", "bpo_core", "addon_x"])
        assert result["monthly_total"] == 380_000  # 30k + 250k + 100k

    def test_empty_modules(self):
        result = _calc_pricing([])
        assert result["monthly_total"] == 0
        assert result["tax"] == 0


class TestMakeProposalNumber:
    def test_format(self):
        number = _make_proposal_number()
        assert number.startswith("PR-")
        parts = number.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 6   # YYYYMM
        assert len(parts[2]) == 4   # XXXX


class TestParseLlmJson:
    def test_plain_json(self):
        raw = '{"key": "value"}'
        result = _parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_with_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = _parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_with_plain_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = _parse_llm_json(raw)
        assert result == {"key": "value"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_json("not json")


class TestLoadGenome:
    def test_returns_dict_for_construction(self):
        result = _load_genome("construction")
        # construction.json が存在すれば中身が返る
        assert isinstance(result, dict)

    def test_returns_empty_for_unknown_industry(self):
        result = _load_genome("nonexistent_industry_xyz")
        assert result == {}


# ────────────────────────────────────────────────────────────
# パイプライン 正常系テスト
# ────────────────────────────────────────────────────────────

class TestProposalGenerationPipelineHappyPath:

    @pytest.mark.asyncio
    async def test_dry_run_completes_8_steps(self):
        """dry_run=True で 8 ステップ全て完了し、成功フラグが True になる。"""
        llm_response = MagicMock()
        llm_response.content = json.dumps(MOCK_PROPOSAL_JSON)
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 800
        llm_response.tokens_out = 400
        llm_response.model_used = "gemini-2.5-pro"

        mail_response = MagicMock()
        mail_response.content = json.dumps({
            "subject": "【ご提案書】テスト建設株式会社 様へ",
            "body_html": "<p>ご確認ください。</p>",
        })
        mail_response.cost_yen = 0.2
        mail_response.tokens_in = 200
        mail_response.tokens_out = 100
        mail_response.model_used = "gemini-2.5-flash"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[llm_response, mail_response])

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  new_callable=AsyncMock, return_value=_make_rule_matcher_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_pdf_generator",
                  return_value=_make_pdf_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline._upload_pdf_to_storage",
                  new_callable=AsyncMock, return_value=f"{COMPANY_ID}/PR-202603-ABCD.pdf"),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_writer",
                  return_value=_make_saas_writer_output()),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "lead_id": LEAD_ID,
                    "opportunity_id": OPP_ID,
                },
                dry_run=True,
            )

        assert result.success is True
        assert result.failed_step is None
        assert len(result.steps) == 8

        step_names = [s.step_name for s in result.steps]
        assert "saas_reader" in step_names
        assert "rule_matcher" in step_names
        assert "document_generator" in step_names
        assert "pdf_generator" in step_names
        assert "message" in step_names

        assert result.total_cost_yen > 0

    @pytest.mark.asyncio
    async def test_final_output_contains_required_keys(self):
        """final_output に必須キーが揃っている。"""
        llm_response = MagicMock()
        llm_response.content = json.dumps(MOCK_PROPOSAL_JSON)
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 800
        llm_response.tokens_out = 400
        llm_response.model_used = "gemini-2.5-pro"

        mail_response = MagicMock()
        mail_response.content = json.dumps({
            "subject": "提案書送付",
            "body_html": "<p>本文</p>",
        })
        mail_response.cost_yen = 0.2
        mail_response.tokens_in = 200
        mail_response.tokens_out = 100
        mail_response.model_used = "gemini-2.5-flash"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[llm_response, mail_response])

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  new_callable=AsyncMock, return_value=_make_rule_matcher_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_pdf_generator",
                  return_value=_make_pdf_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline._upload_pdf_to_storage",
                  new_callable=AsyncMock, return_value=f"{COMPANY_ID}/PR-202603-ABCD.pdf"),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_writer",
                  return_value=_make_saas_writer_output()),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={"lead_id": LEAD_ID},
                dry_run=True,
            )

        fo = result.final_output
        for key in ("proposal_number", "proposal_id", "pdf_storage_path", "email_sent", "industry"):
            assert key in fo, f"final_output に '{key}' がない"

        assert fo["industry"] == "construction"
        assert fo["proposal_number"].startswith("PR-")

    @pytest.mark.asyncio
    async def test_industry_label_mapping(self):
        """業種コードが日本語ラベルに正しく変換される。"""
        for industry_code, label in INDUSTRY_LABELS.items():
            assert label, f"industry '{industry_code}' のラベルが空"


# ────────────────────────────────────────────────────────────
# パイプライン エラー系テスト
# ────────────────────────────────────────────────────────────

class TestProposalGenerationPipelineErrorCases:

    @pytest.mark.asyncio
    async def test_missing_lead_id_fails_step1(self):
        """lead_id 未指定時は Step 1 で失敗する。"""
        result = await run_proposal_generation_pipeline(
            company_id=COMPANY_ID,
            input_data={},  # lead_id なし
        )
        assert result.success is False
        assert result.failed_step == "saas_reader"
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_llm_json_parse_error_fails_step3(self):
        """LLM が不正な JSON を返した場合は Step 3 で失敗する。"""
        bad_llm_response = MagicMock()
        bad_llm_response.content = "これは JSON ではありません"
        bad_llm_response.cost_yen = 0.5
        bad_llm_response.tokens_in = 200
        bad_llm_response.tokens_out = 50
        bad_llm_response.model_used = "gemini-2.5-pro"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=bad_llm_response)

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  new_callable=AsyncMock, return_value=_make_rule_matcher_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={"lead_id": LEAD_ID},
                dry_run=True,
            )

        assert result.success is False
        assert result.failed_step == "document_generator"

    @pytest.mark.asyncio
    async def test_pdf_generation_failure_fails_step4(self):
        """PDF 生成失敗で Step 4 で停止し、それ以降のステップは実行されない。"""
        llm_response = MagicMock()
        llm_response.content = json.dumps(MOCK_PROPOSAL_JSON)
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 800
        llm_response.tokens_out = 400
        llm_response.model_used = "gemini-2.5-pro"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  new_callable=AsyncMock, return_value=_make_rule_matcher_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_pdf_generator",
                  return_value=_make_pdf_output(success=False)),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={"lead_id": LEAD_ID},
                dry_run=True,
            )

        assert result.success is False
        assert result.failed_step == "pdf_generator"
        # Step 5 以降は実行されていない
        executed_step_names = [s.step_name for s in result.steps]
        assert "saas_writer_storage" not in executed_step_names

    @pytest.mark.asyncio
    async def test_email_send_failure_is_nonfatal(self):
        """メール送信失敗はパイプライン全体の失敗にならない（非致命的）。"""
        llm_response = MagicMock()
        llm_response.content = json.dumps(MOCK_PROPOSAL_JSON)
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 800
        llm_response.tokens_out = 400
        llm_response.model_used = "gemini-2.5-pro"

        mail_response = MagicMock()
        mail_response.content = json.dumps({
            "subject": "提案書送付",
            "body_html": "<p>本文</p>",
        })
        mail_response.cost_yen = 0.2
        mail_response.tokens_in = 200
        mail_response.tokens_out = 100
        mail_response.model_used = "gemini-2.5-flash"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[llm_response, mail_response])

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  new_callable=AsyncMock, return_value=_make_rule_matcher_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_pdf_generator",
                  return_value=_make_pdf_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline._upload_pdf_to_storage",
                  new_callable=AsyncMock, return_value=f"{COMPANY_ID}/PR-202603-ABCD.pdf"),
            # SendGrid 送信失敗をシミュレート
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline._send_email_via_sendgrid",
                  new_callable=AsyncMock, return_value=False),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_writer",
                  return_value=_make_saas_writer_output()),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={"lead_id": LEAD_ID, "opportunity_id": OPP_ID},
                dry_run=False,
            )

        # パイプライン全体は成功（メール失敗は非致命的）
        assert result.success is True
        # Step 7 に警告が記録されている
        step7 = next(s for s in result.steps if s.step_name == "saas_writer_email")
        assert step7.warning is not None


# ────────────────────────────────────────────────────────────
# confidence warning テスト
# ────────────────────────────────────────────────────────────

class TestConfidenceWarning:

    @pytest.mark.asyncio
    async def test_low_confidence_step_gets_warning(self):
        """confidence が CONFIDENCE_WARNING_THRESHOLD 未満のステップに warning が付く。"""
        from workers.micro.models import MicroAgentOutput

        # rule_matcher を low confidence で返す
        low_conf_output = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={"matched_rules": [], "applied_values": {}, "unmatched_fields": []},
            confidence=0.40,  # 閾値 0.70 未満
            cost_yen=0.0, duration_ms=10,
        )

        llm_response = MagicMock()
        llm_response.content = json.dumps(MOCK_PROPOSAL_JSON)
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 800
        llm_response.tokens_out = 400
        llm_response.model_used = "gemini-2.5-pro"

        mail_response = MagicMock()
        mail_response.content = json.dumps({
            "subject": "提案書送付",
            "body_html": "<p>本文</p>",
        })
        mail_response.cost_yen = 0.2
        mail_response.tokens_in = 200
        mail_response.tokens_out = 100
        mail_response.model_used = "gemini-2.5-flash"

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(side_effect=[llm_response, mail_response])

        with (
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_reader",
                  side_effect=_make_saas_reader_side_effect(MOCK_LEAD, MOCK_OPPORTUNITY)),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_rule_matcher",
                  return_value=low_conf_output),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.get_llm_client",
                  return_value=mock_llm),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_pdf_generator",
                  return_value=_make_pdf_output()),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline._upload_pdf_to_storage",
                  new_callable=AsyncMock, return_value=f"{COMPANY_ID}/PR-202603-ABCD.pdf"),
            patch("workers.bpo.sales.pipelines.proposal_generation_pipeline.run_saas_writer",
                  return_value=_make_saas_writer_output()),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=COMPANY_ID,
                input_data={"lead_id": LEAD_ID},
                dry_run=True,
            )

        rule_matcher_step = next(s for s in result.steps if s.step_name == "rule_matcher")
        assert rule_matcher_step.warning is not None
        assert "confidence低" in rule_matcher_step.warning


# ────────────────────────────────────────────────────────────
# summary() メソッドテスト
# ────────────────────────────────────────────────────────────

class TestProposalGenerationResultSummary:

    def test_summary_success(self):
        result = ProposalGenerationResult(
            success=True,
            steps=[
                StepResult(1, "saas_reader", "saas_reader", True, {}, 0.9, 0.0, 10),
                StepResult(2, "rule_matcher", "rule_matcher", True, {}, 0.65, 0.0, 15,
                           warning="confidence低 (0.65 < 0.70)"),
            ],
            final_output={"proposal_number": "PR-202603-ABCD"},
            total_cost_yen=1.7,
            total_duration_ms=250,
        )
        summary = result.summary()
        assert "OK" in summary
        assert "2/8" in summary
        assert "1.70" in summary
        assert "250ms" in summary
        assert "confidence低" in summary

    def test_summary_failure(self):
        result = ProposalGenerationResult(
            success=False,
            steps=[
                StepResult(1, "saas_reader", "saas_reader", False,
                           {"error": "DB error"}, 0.0, 0.0, 5),
            ],
            final_output={},
            total_cost_yen=0.0,
            total_duration_ms=10,
            failed_step="saas_reader",
        )
        summary = result.summary()
        assert "NG" in summary
        assert "失敗ステップ: saas_reader" in summary

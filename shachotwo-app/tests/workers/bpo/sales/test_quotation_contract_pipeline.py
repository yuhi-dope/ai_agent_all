"""
SFA パイプライン③ — 見積書・契約書自動送付 テスト

対象: workers/bpo/sales/pipelines/quotation_contract_pipeline.py

テスト方針:
- 外部依存（Supabase / WeasyPrint / CloudSign / freee / SendGrid）は全てモック
- Phase A（見積）と Phase B（契約）の分岐を独立してテスト
- 料金計算ロジックは純粋関数として単体テスト
- dry_run=True で外部書き込みをスキップするパスを確認
- 承認ステータスによる分岐（approved / pending / rejected / revision_requested）を確認
"""
from __future__ import annotations

import sys
import types
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

# WeasyPrint が CI 環境に存在しない場合のスタブ
_weasyprint_stub = types.ModuleType("weasyprint")
_weasyprint_stub.HTML = MagicMock(return_value=MagicMock(write_pdf=MagicMock(return_value=b"%PDF-1.4 test")))
sys.modules.setdefault("weasyprint", _weasyprint_stub)

from workers.bpo.sales.sfa.quotation_contract_pipeline import (
    run_quotation_contract_pipeline,
    QuotationContractResult,
    StepRecord,
    _step1_calculate_quotation,
    _step4_check_approval,
    _build_plan_name,
    _generate_quotation_number,
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_REVISION_REQUESTED,
    PRICE_BRAIN,
    PRICE_BPO_CORE,
    PRICE_ADDITIONAL,
)

COMPANY_ID = "test-company-sales-001"
OPP_ID = "opp-uuid-quotation-0001"

BASE_INPUT: dict = {
    "opportunity_id": OPP_ID,
    "selected_modules": ["brain", "bpo_core"],
    "target_company_name": "テスト建設株式会社",
    "contact_name": "山田太郎",
    "contact_email": "yamada@test-construction.co.jp",
    "billing_cycle": "monthly",
    "referral": False,
}


# ────────────────────────────────────────────────────────────
# ユニットテスト: _step1_calculate_quotation
# ────────────────────────────────────────────────────────────

class TestQuotationCalculator:
    """見積金額計算の単体テスト（LLM不使用）。"""

    @pytest.mark.asyncio
    async def test_brain_only(self):
        """ブレインのみ選択。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, ["brain"], "monthly", False
        )
        assert step.success
        assert calc["subtotal"] == PRICE_BRAIN
        assert calc["tax"] == int(Decimal(str(PRICE_BRAIN)) * Decimal("0.10"))
        assert calc["total"] == calc["subtotal"] + calc["tax"]
        assert len(calc["line_items"]) == 1
        assert calc["line_items"][0]["name"] == "ブレイン（デジタルツイン・Q&A）"

    @pytest.mark.asyncio
    async def test_brain_and_bpo_core(self):
        """ブレイン + BPOコア。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, ["brain", "bpo_core"], "monthly", False
        )
        expected_subtotal = PRICE_BRAIN + PRICE_BPO_CORE
        assert step.success
        assert calc["subtotal"] == expected_subtotal
        assert len(calc["line_items"]) == 2

    @pytest.mark.asyncio
    async def test_additional_modules(self):
        """追加モジュール2個。"""
        modules = ["brain", "bpo_core", "bpo_additional_1", "bpo_additional_2"]
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, modules, "monthly", False
        )
        expected_subtotal = PRICE_BRAIN + PRICE_BPO_CORE + PRICE_ADDITIONAL * 2
        assert step.success
        assert calc["subtotal"] == expected_subtotal
        # 明細: ブレイン + BPOコア + 追加モジュール×2
        assert len(calc["line_items"]) == 3

    @pytest.mark.asyncio
    async def test_annual_discount_10percent(self):
        """年払い10%割引が正確に適用されること。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, ["brain", "bpo_core"], "annual", False
        )
        base = PRICE_BRAIN + PRICE_BPO_CORE
        discount = int(Decimal(str(base)) * Decimal("0.10"))
        expected_subtotal = base - discount
        assert step.success
        assert calc["subtotal"] == expected_subtotal
        # 割引行が追加されていること
        discount_items = [li for li in calc["line_items"] if "割引" in li["name"]]
        assert len(discount_items) == 1
        assert discount_items[0]["amount"] == -discount

    @pytest.mark.asyncio
    async def test_referral_discount_first_month_free(self):
        """紹介割引: 初月無料（subtotal が 0 になる）。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, ["brain"], "monthly", True
        )
        assert step.success
        assert calc["subtotal"] == 0
        assert calc["tax"] == 0
        assert calc["total"] == 0
        referral_items = [li for li in calc["line_items"] if "紹介" in li["name"]]
        assert len(referral_items) == 1

    @pytest.mark.asyncio
    async def test_annual_and_referral_combined(self):
        """年払い割引後に紹介割引を適用。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, ["brain", "bpo_core"], "annual", True
        )
        assert step.success
        # 年払いOFF後にさらに初月無料 → subtotal=0
        assert calc["subtotal"] == 0

    @pytest.mark.asyncio
    async def test_empty_modules_fails(self):
        """モジュール未選択はエラー。"""
        step, calc = await _step1_calculate_quotation(
            COMPANY_ID, [], "monthly", False
        )
        assert not step.success
        assert "error" in step.result

    @pytest.mark.asyncio
    async def test_cost_is_zero(self):
        """LLM不使用なのでコストは0円。"""
        step, _ = await _step1_calculate_quotation(
            COMPANY_ID, ["brain"], "monthly", False
        )
        assert step.cost_yen == 0.0

    @pytest.mark.asyncio
    async def test_confidence_is_1(self):
        """計算結果の confidence は1.0。"""
        step, _ = await _step1_calculate_quotation(
            COMPANY_ID, ["brain", "bpo_core"], "monthly", False
        )
        assert step.confidence == 1.0


# ────────────────────────────────────────────────────────────
# ユニットテスト: _step4_check_approval
# ────────────────────────────────────────────────────────────

class TestApprovalChecker:
    """承認確認ステップの単体テスト。"""

    @pytest.mark.asyncio
    async def test_approved_returns_true(self):
        step = await _step4_check_approval(COMPANY_ID, "QT-202603-0001", APPROVAL_APPROVED)
        assert step.success
        assert step.result["approved"] is True
        assert step.result["approval_status"] == APPROVAL_APPROVED

    @pytest.mark.asyncio
    async def test_pending_returns_false(self):
        step = await _step4_check_approval(COMPANY_ID, "QT-202603-0001", APPROVAL_PENDING)
        assert step.success
        assert step.result["approved"] is False
        assert step.warning is not None  # 承認待ちメッセージがある

    @pytest.mark.asyncio
    async def test_rejected_returns_false_with_warning(self):
        step = await _step4_check_approval(COMPANY_ID, "QT-202603-0001", APPROVAL_REJECTED)
        assert step.success
        assert step.result["approved"] is False
        assert "却下" in (step.warning or "")

    @pytest.mark.asyncio
    async def test_revision_requested_returns_false_with_warning(self):
        step = await _step4_check_approval(COMPANY_ID, "QT-202603-0001", APPROVAL_REVISION_REQUESTED)
        assert step.success
        assert step.result["approved"] is False
        assert "修正" in (step.warning or "")

    @pytest.mark.asyncio
    async def test_unknown_status_falls_back_to_pending(self):
        step = await _step4_check_approval(COMPANY_ID, "QT-202603-0001", "UNKNOWN_STATUS")
        assert step.result["approval_status"] == APPROVAL_PENDING
        assert step.result["approved"] is False


# ────────────────────────────────────────────────────────────
# ユニットテスト: ヘルパー関数
# ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_build_plan_name_brain_only(self):
        assert _build_plan_name(["brain"]) == "ブレイン"

    def test_build_plan_name_full(self):
        name = _build_plan_name(["brain", "bpo_core", "bpo_additional_1", "bpo_additional_2"])
        assert "ブレイン" in name
        assert "BPOコア" in name
        assert "×2" in name

    def test_build_plan_name_empty(self):
        assert _build_plan_name([]) == "カスタムプラン"

    def test_quotation_number_format(self):
        num = _generate_quotation_number()
        assert num.startswith("QT-")
        parts = num.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 6  # YYYYMM
        assert len(parts[2]) == 4  # XXXX


# ────────────────────────────────────────────────────────────
# 統合テスト: Phase A のみ（承認待ち）
# ────────────────────────────────────────────────────────────

class TestPhaseA:
    """Phase A の統合テスト（PDF生成・メール送付をモック）。"""

    def _make_pdf_mock(self):
        """pdf_generator の成功モックを返す。"""
        from workers.micro.models import MicroAgentOutput
        return AsyncMock(return_value=MicroAgentOutput(
            agent_name="pdf_generator",
            success=True,
            result={
                "pdf_bytes": b"%PDF-1.4 test",
                "size_kb": 12.3,
                "template_name": "quotation_template.html",
                "quotation_number": "QT-202603-0001",
                "valid_until": "2026-04-20",
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=150,
        ))

    def _make_writer_mock(self):
        """saas_writer の成功モックを返す。"""
        from workers.micro.models import MicroAgentOutput
        return AsyncMock(return_value=MicroAgentOutput(
            agent_name="saas_writer",
            success=True,
            result={"success": True, "operation_id": "test-op-001", "dry_run": False},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=50,
        ))

    @pytest.mark.asyncio
    async def test_phase_a_pending_stops_at_step4(self):
        """approval_status=pending で Phase A (Step 1-4) が完了し、Phase B に進まない。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_PENDING,
            )

        assert result.success is True
        assert result.phase == "phase_a"
        assert len(result.steps) == 4
        assert result.approval_status == APPROVAL_PENDING
        assert result.contract_id is None
        assert result.cloudsign_document_id is None
        step_names = [s.step_name for s in result.steps]
        assert "quotation_calculator" in step_names
        assert "approval_checker" in step_names

    @pytest.mark.asyncio
    async def test_phase_a_rejected_stops_at_step4(self):
        """approval_status=rejected で Phase A 完了後に Phase B に進まない。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_REJECTED,
            )

        assert result.success is True
        assert result.phase == "phase_a"
        assert result.approval_status == APPROVAL_REJECTED

    @pytest.mark.asyncio
    async def test_missing_required_fields_returns_error(self):
        """必須フィールド欠如はパイプライン開始前に失敗。"""
        bad_input = {
            "opportunity_id": OPP_ID,
            # contact_email が欠如
            "selected_modules": ["brain"],
            "target_company_name": "テスト株式会社",
        }
        result = await run_quotation_contract_pipeline(
            company_id=COMPANY_ID,
            input_data=bad_input,
            approval_status=APPROVAL_PENDING,
        )
        assert result.success is False
        assert result.failed_step == "input_validation"

    @pytest.mark.asyncio
    async def test_empty_modules_returns_error(self):
        """selected_modules が空のとき Step 1 で失敗。"""
        bad_input = {**BASE_INPUT, "selected_modules": []}
        result = await run_quotation_contract_pipeline(
            company_id=COMPANY_ID,
            input_data=bad_input,
            approval_status=APPROVAL_PENDING,
        )
        assert result.success is False
        # input_validation で検出される
        assert result.failed_step == "input_validation"

    @pytest.mark.asyncio
    async def test_step1_result_contains_calc_data(self):
        """Step 1 の結果に金額計算データが含まれる。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_PENDING,
            )

        calc_step = next(s for s in result.steps if s.step_name == "quotation_calculator")
        assert calc_step.result["subtotal"] == PRICE_BRAIN + PRICE_BPO_CORE
        assert calc_step.result["tax"] > 0
        assert calc_step.result["total"] > calc_step.result["subtotal"]

    @pytest.mark.asyncio
    async def test_annual_billing_reflected_in_calc(self):
        """年払い選択が見積計算に反映される。"""
        annual_input = {**BASE_INPUT, "billing_cycle": "annual"}
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=annual_input,
                approval_status=APPROVAL_PENDING,
            )

        calc_step = next(s for s in result.steps if s.step_name == "quotation_calculator")
        base = PRICE_BRAIN + PRICE_BPO_CORE
        discount = int(Decimal(str(base)) * Decimal("0.10"))
        assert calc_step.result["subtotal"] == base - discount


# ────────────────────────────────────────────────────────────
# 統合テスト: Phase B（承認済み + dry_run）
# ────────────────────────────────────────────────────────────

class TestPhaseB:
    """Phase B の統合テスト（DB/CloudSign/freee は dry_run でスキップ）。"""

    def _make_pdf_mock(self):
        from workers.micro.models import MicroAgentOutput
        return AsyncMock(return_value=MicroAgentOutput(
            agent_name="pdf_generator",
            success=True,
            result={
                "pdf_bytes": b"%PDF-1.4 test",
                "size_kb": 12.3,
                "template_name": "contract_template.html",
                "quotation_number": "QT-202603-0001",
                "valid_until": "2026-04-20",
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=150,
        ))

    def _make_writer_mock(self):
        from workers.micro.models import MicroAgentOutput
        return AsyncMock(return_value=MicroAgentOutput(
            agent_name="saas_writer",
            success=True,
            result={"success": True, "operation_id": "test-op-001", "dry_run": False},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=50,
        ))

    @pytest.mark.asyncio
    async def test_phase_b_dry_run_completes_8_steps(self):
        """dry_run=True かつ approval_status=approved で 8 ステップが全て完了する。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,  # DB/CloudSign/freee はスキップ
            )

        assert result.success is True
        assert result.phase == "phase_b"
        assert len(result.steps) == 8

        step_names = [s.step_name for s in result.steps]
        assert "quotation_calculator" in step_names
        assert "pdf_generator(quotation)" in step_names
        assert "email_sender(quotation)" in step_names
        assert "approval_checker" in step_names
        assert "pdf_generator(contract)" in step_names
        assert "cloudsign_sender" in step_names
        assert "db_writer" in step_names
        assert "invoice_issuer(freee)" in step_names

    @pytest.mark.asyncio
    async def test_phase_b_dry_run_sets_dry_contract_id(self):
        """dry_run=True の場合 contract_id が DRY- プレフィックスを持つ。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        assert result.contract_id is not None
        assert result.contract_id.startswith("DRY-")

    @pytest.mark.asyncio
    async def test_phase_b_dry_run_skips_cloudsign_with_warning(self):
        """dry_run=True では CloudSign スキップ警告が記録される。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        cloudsign_step = next(
            s for s in result.steps if s.step_name == "cloudsign_sender"
        )
        assert cloudsign_step.success is True
        assert cloudsign_step.result.get("skipped") is True
        assert "dry_run" in (cloudsign_step.warning or "")

    @pytest.mark.asyncio
    async def test_phase_b_final_output_contains_expected_keys(self):
        """Phase B 完了時の final_output に必要なキーが揃っている。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        for key in ["phase", "quotation_number", "contract_id", "calc", "opportunity_stage"]:
            assert key in result.final_output
        assert result.final_output["opportunity_stage"] == "won"
        assert result.final_output["phase"] == "phase_b"

    @pytest.mark.asyncio
    async def test_phase_b_cost_aggregated_across_all_steps(self):
        """total_cost_yen が全ステップのコストの合算である。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        expected_total = sum(s.cost_yen for s in result.steps)
        assert result.total_cost_yen == pytest.approx(expected_total)

    @pytest.mark.asyncio
    async def test_summary_str_contains_all_steps(self):
        """summary() が 8 ステップ分の情報を含む文字列を返す。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        summary = result.summary()
        assert "phase_b" in summary
        assert "quotation_calculator" in summary
        assert "cloudsign_sender" in summary
        assert "invoice_issuer" in summary

    @pytest.mark.asyncio
    async def test_no_cloudsign_credentials_skips_step6(self):
        """cloudsign_credentials=None でも Step 6 はスキップされて pipeline は成功する。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT,
                cloudsign_credentials=None,  # 未設定
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        assert result.success is True
        cloudsign_step = next(s for s in result.steps if s.step_name == "cloudsign_sender")
        assert cloudsign_step.result.get("skipped") is True

    @pytest.mark.asyncio
    async def test_referral_discount_reduces_freee_invoice_amount(self):
        """紹介割引を適用した場合でも freee Step まで到達する。"""
        referral_input = {**BASE_INPUT, "referral": True}
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
            self._make_pdf_mock(),
        ), patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
            self._make_writer_mock(),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=COMPANY_ID,
                input_data=referral_input,
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        assert result.success is True
        assert len(result.steps) == 8
        calc_step = next(s for s in result.steps if s.step_name == "quotation_calculator")
        assert calc_step.result["subtotal"] == 0  # 初月無料


# ────────────────────────────────────────────────────────────
# PIPELINE_REGISTRY 登録確認
# ────────────────────────────────────────────────────────────

class TestPipelineRegistry:
    def test_quotation_contract_in_registry(self):
        from workers.bpo.sales.pipelines import PIPELINE_REGISTRY
        assert "quotation_contract_pipeline" in PIPELINE_REGISTRY

    def test_registry_entry_has_required_keys(self):
        from workers.bpo.sales.pipelines import PIPELINE_REGISTRY
        entry = PIPELINE_REGISTRY["quotation_contract_pipeline"]
        for key in ["module", "function", "industry", "trigger", "steps", "description"]:
            assert key in entry

    def test_registry_steps_count_is_8(self):
        from workers.bpo.sales.pipelines import PIPELINE_REGISTRY
        assert PIPELINE_REGISTRY["quotation_contract_pipeline"]["steps"] == 8

    def test_registry_trigger_mentions_proposal_accepted(self):
        from workers.bpo.sales.pipelines import PIPELINE_REGISTRY
        trigger = PIPELINE_REGISTRY["quotation_contract_pipeline"]["trigger"]
        assert "proposal_accepted" in trigger

"""
経理系7パイプライン テスト

対象:
  - invoice_issue_pipeline    (請求書発行)
  - ar_management_pipeline    (売掛管理・入金消込)
  - ap_management_pipeline    (買掛管理・支払処理)
  - bank_reconciliation_pipeline (銀行照合)
  - journal_entry_pipeline    (仕訳入力)
  - monthly_close_pipeline    (月次決算)
  - tax_filing_pipeline       (税務申告支援)

各パイプラインに対して:
  - 正常系（直接渡し入力）: 全ステップ成功・期待フィールドの存在確認
  - 計算ロジック: 請求金額・消費税・前月比差異等
  - 異常検知: 金額0、差異あり、高税負担率など
  - 失敗伝搬: saas_reader 失敗 → emit_fail

外部API（LLM / freee / 銀行API）は全てモック。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from workers.micro.models import MicroAgentOutput

COMPANY_ID = str(uuid4())


# ────────────────────────────────────────────────────────────────────
# ヘルパー: 成功 MicroAgentOutput を返すデフォルトモック
# ────────────────────────────────────────────────────────────────────

def _ok(agent_name: str = "mock", result: dict | None = None) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=result or {},
        confidence=0.95,
        cost_yen=1.0,
        duration_ms=10,
    )


def _fail(agent_name: str = "mock") -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=False,
        result={"error": "mock failure"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=0,
    )


# ════════════════════════════════════════════════════════════════════
# 1. 請求書発行パイプライン（invoice_issue）
# ════════════════════════════════════════════════════════════════════

class TestInvoiceIssuePipeline:

    @pytest.mark.asyncio
    async def test_direct_invoice_success(self):
        """直接渡し形式（invoices キー）で全ステップが成功する。"""
        from workers.bpo.common.pipelines.invoice_issue_pipeline import (
            InvoiceIssuePipelineResult,
            run_invoice_issue_pipeline,
        )

        invoice_items = [
            {"name": "設計費", "quantity": 1, "unit_price": 500_000, "tax_rate": 0.10},
        ]
        input_data = {
            "invoices": [{"client_name": "株式会社テスト", "items": invoice_items, "invoice_number": ""}],
            "dry_run": True,
        }

        with patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {
                       "client_name": "株式会社テスト",
                       "items": invoice_items,
                       "invoice_number": "",
                       "notes": "",
                   }))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok("cost_calculator", {"total": 550_000}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_pdf_generator",
                   new=AsyncMock(return_value=_ok("pdf_generator", {"pdf_path": "/tmp/inv.pdf"}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok("saas_writer", {"requires_approval": False}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok("output_validator", {"valid": True}))):

            result = await run_invoice_issue_pipeline(COMPANY_ID, input_data)

        assert isinstance(result, InvoiceIssuePipelineResult)
        assert result.success is True
        assert result.approval_required is True
        assert len(result.steps) == 7
        assert result.failed_step is None

    @pytest.mark.asyncio
    async def test_consumption_tax_calculation(self):
        """消費税計算ロジック: 単価300,000 × 1個 × 10% = 30,000 の整合確認。"""
        from workers.bpo.common.pipelines.invoice_issue_pipeline import run_invoice_issue_pipeline

        items = [{"name": "コンサル費", "quantity": 1, "unit_price": 300_000, "tax_rate": 0.10}]
        input_data = {"invoices": [{"client_name": "甲社", "items": items, "invoice_number": ""}], "dry_run": True}

        with patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {
                       "client_name": "甲社", "items": items, "invoice_number": "", "notes": "",
                   }))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_pdf_generator",
                   new=AsyncMock(return_value=_ok("pdf_generator", {"pdf_path": "/tmp/inv.pdf"}))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok("saas_writer"))), \
             patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_invoice_issue_pipeline(COMPANY_ID, input_data)

        # 計算は pipeline 内で Decimal で行われる
        assert result.total_amount == Decimal("330000")  # 300,000 + 30,000

    @pytest.mark.asyncio
    async def test_extractor_failure_returns_fail(self):
        """extractor が失敗した場合に failed_step='extractor' で返る。"""
        from workers.bpo.common.pipelines.invoice_issue_pipeline import run_invoice_issue_pipeline

        input_data = {"invoices": [{}], "dry_run": True}

        with patch("workers.bpo.common.pipelines.invoice_issue_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_fail("structured_extractor"))):
            result = await run_invoice_issue_pipeline(COMPANY_ID, input_data)

        assert result.success is False
        assert result.failed_step == "extractor"


# ════════════════════════════════════════════════════════════════════
# 2. 売掛管理・入金消込パイプライン（ar_management）
# ════════════════════════════════════════════════════════════════════

class TestARManagementPipeline:

    @pytest.mark.asyncio
    async def test_direct_input_success(self):
        """直接渡し形式で正常完了。消込済み・未消込のカウントを確認。"""
        from workers.bpo.common.pipelines.ar_management_pipeline import (
            ARManagementPipelineResult,
            run_ar_management_pipeline,
        )

        unpaid = [
            {"invoice_number": "INV-001", "client_name": "A社", "amount": 110_000,
             "due_date": "2026-01-31"},
        ]
        bank_txns = [
            {"date": "2026-02-01", "amount": 110_000, "payer": "A社"},
        ]

        with patch("workers.bpo.common.pipelines.ar_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "matched": [{"invoice_number": "INV-001"}],
                       "unmatched_invoices": [],
                   }))), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {"1_30_days": 0, "total": 0}))), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_message_drafter",
                   new=AsyncMock(return_value=type("D", (), {"subject": "督促", "body": "..."})())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok())):

            result = await run_ar_management_pipeline(
                COMPANY_ID,
                {"unpaid_invoices": unpaid, "bank_transactions": bank_txns, "dry_run": True},
            )

        assert isinstance(result, ARManagementPipelineResult)
        assert result.success is True
        assert result.matched_count == 1
        assert result.unmatched_count == 0
        assert len(result.steps) == 7

    @pytest.mark.asyncio
    async def test_overdue_invoice_triggers_dunning(self):
        """支払期日超過の請求書があると dunning_actions に追加される。"""
        from workers.bpo.common.pipelines.ar_management_pipeline import run_ar_management_pipeline

        overdue_inv = [
            {"invoice_number": "INV-002", "client_name": "B社", "amount": 220_000,
             "due_date": "2025-10-01"},  # 大幅超過
        ]

        with patch("workers.bpo.common.pipelines.ar_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "matched": [],
                       "unmatched_invoices": overdue_inv,
                   }))), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {"total": 220_000}))), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_message_drafter",
                   new=AsyncMock(return_value=type("D", (), {"subject": "督促", "body": "..."})())), \
             patch("workers.bpo.common.pipelines.ar_management_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok())):

            result = await run_ar_management_pipeline(
                COMPANY_ID,
                {"unpaid_invoices": overdue_inv, "bank_transactions": [], "dry_run": True},
            )

        assert result.success is True
        assert len(result.dunning_actions) == 1
        # 大幅超過なので legal_warning か demand_letter ステージになる
        assert result.dunning_actions[0]["stage"] in ("legal_warning", "demand_letter", "second_notice")
        assert result.approval_required is True

    @pytest.mark.asyncio
    async def test_rule_matcher_failure_returns_fail(self):
        """rule_matcher 失敗 → failed_step='rule_matcher'。"""
        from workers.bpo.common.pipelines.ar_management_pipeline import run_ar_management_pipeline

        with patch("workers.bpo.common.pipelines.ar_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_fail("rule_matcher"))):

            result = await run_ar_management_pipeline(
                COMPANY_ID,
                {"unpaid_invoices": [], "bank_transactions": [], "dry_run": True},
            )

        assert result.success is False
        assert result.failed_step == "rule_matcher"


# ════════════════════════════════════════════════════════════════════
# 3. 買掛管理・支払処理パイプライン（ap_management）
# ════════════════════════════════════════════════════════════════════

class TestAPManagementPipeline:

    @pytest.mark.asyncio
    async def test_direct_payables_success(self):
        """直接渡し形式で全8ステップが成功する。"""
        from workers.bpo.common.pipelines.ap_management_pipeline import (
            APManagementPipelineResult,
            run_ap_management_pipeline,
        )

        payables = [
            {"vendor_name": "部品商社", "invoice_number": "P-001",
             "amount": 330_000, "due_date": "2026-04-30",
             "purchase_order_number": "PO-100"},
        ]

        with patch("workers.bpo.common.pipelines.ap_management_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {
                       "vendor_name": "部品商社", "invoice_number": "P-001",
                       "amount": 330_000, "due_date": "2026-04-30",
                   }))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "match_ok": payables, "match_ng": [],
                   }))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"file_path": "/tmp/zengin.txt"}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_ap_management_pipeline(
                COMPANY_ID,
                {"payables": payables, "dry_run": True},
            )

        assert isinstance(result, APManagementPipelineResult)
        assert result.success is True
        assert result.three_way_match_ok == 1
        assert result.three_way_match_ng == 0
        assert result.approval_required is True
        assert len(result.steps) == 8

    @pytest.mark.asyncio
    async def test_early_payment_discount_applied(self):
        """支払期日10日以上前なら2%の早期割引が適用される。"""
        from workers.bpo.common.pipelines.ap_management_pipeline import run_ap_management_pipeline
        from datetime import date, timedelta

        future_date = (date.today() + timedelta(days=15)).isoformat()
        payables = [
            {"vendor_name": "仕入先X", "invoice_number": "P-002",
             "total_amount": 100_000, "due_date": future_date},
        ]

        with patch("workers.bpo.common.pipelines.ap_management_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", payables[0]))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "match_ok": payables, "match_ng": [],
                   }))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"file_path": "/tmp/z.txt"}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_ap_management_pipeline(
                COMPANY_ID,
                {"payables": payables, "dry_run": True},
            )

        assert result.success is True
        # 早期割引 2% = 2,000円の節約
        assert result.early_payment_savings == Decimal("2000")

    @pytest.mark.asyncio
    async def test_three_way_match_ng_adds_alert(self):
        """三者照合NGがあると compliance_alerts にアラートが追加される。"""
        from workers.bpo.common.pipelines.ap_management_pipeline import run_ap_management_pipeline

        payables = [{"vendor_name": "怪しい業者", "invoice_number": "P-003", "amount": 50_000, "due_date": "2026-04-01"}]

        with patch("workers.bpo.common.pipelines.ap_management_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", payables[0]))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "match_ok": [], "match_ng": payables,
                   }))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"file_path": "/tmp/z.txt"}))), \
             patch("workers.bpo.common.pipelines.ap_management_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_ap_management_pipeline(
                COMPANY_ID,
                {"payables": payables, "dry_run": True},
            )

        assert result.three_way_match_ng == 1
        assert any("三者照合NG" in a for a in result.compliance_alerts)


# ════════════════════════════════════════════════════════════════════
# 4. 銀行照合パイプライン（bank_reconciliation）
# ════════════════════════════════════════════════════════════════════

class TestBankReconciliationPipeline:

    @pytest.mark.asyncio
    async def test_perfect_reconciliation(self):
        """全件マッチして差額ゼロ → reconciled=True。"""
        from workers.bpo.common.pipelines.bank_reconciliation_pipeline import (
            BankReconciliationPipelineResult,
            run_bank_reconciliation_pipeline,
        )

        bank_txns = [{"date": "2026-03-01", "amount": 100_000, "description": "売上入金"}]
        book_txns = [{"date": "2026-03-01", "amount": 100_000, "description": "売上入金"}]

        with patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "matched": bank_txns, "unmatched_bank": [], "unmatched_book": [],
                   }))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {"items": []}))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/recon.pdf"}))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_bank_reconciliation_pipeline(
                COMPANY_ID,
                {
                    "bank_transactions": bank_txns,
                    "book_transactions": book_txns,
                    "bank_balance": 1_000_000,
                    "book_balance": 1_000_000,  # 一致
                },
            )

        assert isinstance(result, BankReconciliationPipelineResult)
        assert result.success is True
        assert result.reconciled is True
        assert result.approval_required is False

    @pytest.mark.asyncio
    async def test_discrepancy_sets_approval_required(self):
        """差異がある場合 reconciled=False かつ approval_required=True。"""
        from workers.bpo.common.pipelines.bank_reconciliation_pipeline import run_bank_reconciliation_pipeline

        with patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "matched": [], "unmatched_bank": [{"amount": 50_000, "source": "bank"}],
                       "unmatched_book": [],
                   }))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_structured_extractor",
                   new=AsyncMock(return_value=_ok("structured_extractor", {
                       "items": [{"amount": 50_000, "source": "bank", "reason": "unrecorded"}],
                   }))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/r.pdf"}))), \
             patch("workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_bank_reconciliation_pipeline(
                COMPANY_ID,
                {
                    "bank_transactions": [],
                    "book_transactions": [],
                    "bank_balance": 1_050_000,
                    "book_balance": 1_000_000,  # 差異 50,000
                },
            )

        assert result.success is True
        assert result.reconciled is False
        assert result.approval_required is True


# ════════════════════════════════════════════════════════════════════
# 5. 仕訳入力パイプライン（journal_entry）
# ════════════════════════════════════════════════════════════════════

class TestJournalEntryPipeline:

    @pytest.mark.asyncio
    async def test_direct_entries_success(self):
        """entries 直接渡しで全5ステップが成功する。"""
        from workers.bpo.common.pipelines.journal_entry_pipeline import (
            JournalEntryPipelineResult,
            run_journal_entry_pipeline,
        )

        entries = [
            {
                "debit_account": "消耗品費",
                "credit_account": "現金",
                "amount": 5_000,
                "tax_amount": 500,
                "tax_category": "課税仕入",
                "description": "コピー用紙購入",
                "transaction_date": "2026-03-01",
                "counterparty": "文具店",
            }
        ]

        with patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {
                       "matched_rules": [], "pattern_confidence": 0.9,
                   }))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"warnings": [], "passed": True}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok("saas_writer", {"freee_synced": False}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_journal_entry_pipeline(
                COMPANY_ID,
                {"entries": entries, "dry_run": True},
            )

        assert isinstance(result, JournalEntryPipelineResult)
        assert result.success is True
        assert len(result.steps) == 5
        assert result.failed_step is None

    @pytest.mark.asyncio
    async def test_high_amount_sets_approval_required(self):
        """100,000円以上の仕訳は approval_required=True になる。"""
        from workers.bpo.common.pipelines.journal_entry_pipeline import run_journal_entry_pipeline

        entries = [
            {
                "debit_account": "備品",
                "credit_account": "未払金",
                "amount": 200_000,
                "tax_amount": 20_000,
                "tax_category": "課税仕入",
                "description": "PCモニター購入",
                "transaction_date": "2026-03-15",
                "counterparty": "家電量販店",
            }
        ]

        with patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {"matched_rules": [], "pattern_confidence": 0.5}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"warnings": [], "passed": True}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok("saas_writer", {"freee_synced": False}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_journal_entry_pipeline(
                COMPANY_ID,
                {"entries": entries, "dry_run": True},
            )

        assert result.success is True
        assert result.approval_required is True

    @pytest.mark.asyncio
    async def test_extractor_called_for_text_input(self):
        """transaction_text 入力の場合 run_structured_extractor が呼ばれる。"""
        from workers.bpo.common.pipelines.journal_entry_pipeline import run_journal_entry_pipeline

        mock_extractor = AsyncMock(return_value=_ok("structured_extractor", {
            "entries": [
                {"debit_account": "交際費", "credit_account": "現金",
                 "amount": 10_000, "description": "会食費"}
            ],
        }))

        with patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_structured_extractor",
                   new=mock_extractor), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {"matched_rules": [], "pattern_confidence": 0.5}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"warnings": [], "passed": True}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_saas_writer",
                   new=AsyncMock(return_value=_ok("saas_writer", {"freee_synced": False}))), \
             patch("workers.bpo.common.pipelines.journal_entry_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_journal_entry_pipeline(
                COMPANY_ID,
                {"transaction_text": "山田部長と会食 10,000円"},
            )

        assert result.success is True
        mock_extractor.assert_awaited_once()


# ════════════════════════════════════════════════════════════════════
# 6. 月次決算パイプライン（monthly_close）
# ════════════════════════════════════════════════════════════════════

class TestMonthlyClosePipeline:

    @pytest.mark.asyncio
    async def test_direct_trial_balance_success(self):
        """試算表直接渡しで全7ステップが成功する。P&L が正しく計算される。"""
        from workers.bpo.common.pipelines.monthly_close_pipeline import (
            MonthlyClosePipelineResult,
            run_monthly_close_pipeline,
        )

        trial_balance = {
            "revenue": 10_000_000,
            "cogs": 6_000_000,
            "sga": 2_000_000,
        }

        with patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {"unclosed_items": [], "all_clear": True}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/mc.pdf"}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"anomalies": [], "alerts": []}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_monthly_close_pipeline(
                COMPANY_ID,
                {"trial_balance": trial_balance, "target_month": "2026-02"},
            )

        assert isinstance(result, MonthlyClosePipelineResult)
        assert result.success is True
        assert result.pnl["revenue"] == 10_000_000
        assert result.pnl["gross_profit"] == 4_000_000   # 10M - 6M
        assert result.pnl["operating_profit"] == 2_000_000  # 4M - 2M
        assert result.approval_required is True

    @pytest.mark.asyncio
    async def test_anomaly_detected_when_prior_month_differs_over_30pct(self):
        """前月比±30%超の科目が anomalies に追加される。"""
        from workers.bpo.common.pipelines.monthly_close_pipeline import run_monthly_close_pipeline

        trial_balance = {"revenue": 10_000_000, "cogs": 6_000_000, "sga": 2_000_000}
        prior_month = {"revenue": 7_000_000, "cogs": 6_000_000, "sga": 2_000_000}
        # revenue が 10M / 7M = +42.9% → 30%超なのでアラート

        with patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {"unclosed_items": []}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/mc.pdf"}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"anomalies": [], "alerts": []}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_monthly_close_pipeline(
                COMPANY_ID,
                {
                    "trial_balance": trial_balance,
                    "prior_month_pnl": prior_month,
                    "target_month": "2026-02",
                },
            )

        assert result.success is True
        # revenue の前月比超過が anomalies に記録されている
        assert any(a["account"] == "revenue" for a in result.anomalies)
        assert any("前月比±30%超" in a["reason"] for a in result.anomalies)

    @pytest.mark.asyncio
    async def test_bs_imbalance_triggers_alert(self):
        """貸借不一致があると compliance_alerts に記録される。"""
        from workers.bpo.common.pipelines.monthly_close_pipeline import run_monthly_close_pipeline

        trial_balance = {
            "revenue": 5_000_000, "cogs": 3_000_000, "sga": 1_000_000,
            "total_assets": 10_000_000,
            "total_liabilities_equity": 9_000_000,  # 不一致
        }

        with patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_rule_matcher",
                   new=AsyncMock(return_value=_ok("rule_matcher", {"unclosed_items": []}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/mc.pdf"}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"anomalies": [], "alerts": []}))), \
             patch("workers.bpo.common.pipelines.monthly_close_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_monthly_close_pipeline(
                COMPANY_ID,
                {"trial_balance": trial_balance, "target_month": "2026-02"},
            )

        assert result.success is True
        assert any("貸借不一致" in a for a in result.compliance_alerts)


# ════════════════════════════════════════════════════════════════════
# 7. 税務申告支援パイプライン（tax_filing）
# ════════════════════════════════════════════════════════════════════

class TestTaxFilingPipeline:

    @pytest.mark.asyncio
    async def test_consumption_tax_general_method(self):
        """一般課税: 課税売上税額 - 仕入税額控除 = 納付額。"""
        from workers.bpo.common.pipelines.tax_filing_pipeline import (
            TaxFilingPipelineResult,
            run_tax_filing_pipeline,
        )

        # 課税売上高 10M → 消費税1M / 仕入税額控除400K → 納付600K
        annual_data = {
            "taxable_sales": 10_000_000,
            "input_tax_credit": 400_000,
            "pre_tax_income": 2_000_000,
        }

        with patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/tax.pdf"}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_tax_filing_pipeline(
                COMPANY_ID,
                {"annual_data": annual_data, "fiscal_year": "2025"},
            )

        assert isinstance(result, TaxFilingPipelineResult)
        assert result.success is True
        # 消費税: 10M × 10% = 1,000,000 - 400,000 = 600,000
        assert result.consumption_tax["tax_payable"] == 600_000
        assert result.consumption_tax["method"] == "general"
        assert result.tax_accountant_review_required is True

    @pytest.mark.asyncio
    async def test_simplified_tax_method_when_prior_year_under_50m(self):
        """前々事業年度課税売上高5000万以下 → 簡易課税適用。"""
        from workers.bpo.common.pipelines.tax_filing_pipeline import run_tax_filing_pipeline

        annual_data = {
            "taxable_sales": 30_000_000,
            "input_tax_credit": 0,
            "pre_tax_income": 5_000_000,
            "simplified_tax_ratio": "0.50",  # 第5種サービス業
        }

        with patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/tax.pdf"}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_tax_filing_pipeline(
                COMPANY_ID,
                {
                    "annual_data": annual_data,
                    "fiscal_year": "2025",
                    "prior_year_taxable_sales": 20_000_000,  # 5000万以下
                },
            )

        assert result.success is True
        assert result.consumption_tax["method"] == "simplified"
        assert result.consumption_tax["use_simplified"] is True
        # 30M × 10% = 3M × (1 - 0.5) = 1.5M
        assert result.consumption_tax["tax_payable"] == 1_500_000

    @pytest.mark.asyncio
    async def test_corporate_tax_reduced_rate_for_small_income(self):
        """所得800万以下 → 軽減税率15%適用。"""
        from workers.bpo.common.pipelines.tax_filing_pipeline import run_tax_filing_pipeline

        annual_data = {
            "taxable_sales": 10_000_000,
            "input_tax_credit": 0,
            "pre_tax_income": 5_000_000,  # 800万以下
        }

        with patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/tax.pdf"}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_tax_filing_pipeline(
                COMPANY_ID,
                {"annual_data": annual_data, "fiscal_year": "2025"},
            )

        assert result.success is True
        # 5M × 15% = 750,000
        assert result.corporate_tax["estimated_tax"] == 750_000
        assert result.corporate_tax["effective_rate"] == 0.15

    @pytest.mark.asyncio
    async def test_corporate_tax_progressive_above_8m(self):
        """所得800万超 → 800万まで15%、超過分23.2%。"""
        from workers.bpo.common.pipelines.tax_filing_pipeline import run_tax_filing_pipeline

        annual_data = {
            "taxable_sales": 50_000_000,
            "input_tax_credit": 0,
            "pre_tax_income": 10_000_000,  # 800万超
        }

        with patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_cost_calculator",
                   new=AsyncMock(return_value=_ok())), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_document_generator",
                   new=AsyncMock(return_value=_ok("document_generator", {"pdf_path": "/tmp/tax.pdf"}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_compliance_checker",
                   new=AsyncMock(return_value=_ok("compliance_checker", {"alerts": []}))), \
             patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_output_validator",
                   new=AsyncMock(return_value=_ok())):

            result = await run_tax_filing_pipeline(
                COMPANY_ID,
                {"annual_data": annual_data, "fiscal_year": "2025"},
            )

        assert result.success is True
        # 800万 × 15% = 1,200,000 + 200万 × 23.2% = 464,000 → 合計 1,664,000
        assert result.corporate_tax["estimated_tax"] == 1_664_000
        assert result.corporate_tax["effective_rate"] == 0.232

    @pytest.mark.asyncio
    async def test_saas_reader_failure_returns_fail(self):
        """saas_reader 失敗 → failed_step='saas_reader'。"""
        from workers.bpo.common.pipelines.tax_filing_pipeline import run_tax_filing_pipeline

        with patch("workers.bpo.common.pipelines.tax_filing_pipeline.run_saas_reader",
                   new=AsyncMock(return_value=_fail("saas_reader"))):

            result = await run_tax_filing_pipeline(
                COMPANY_ID,
                {"fiscal_year": "2025"},  # annual_data なしで saas_reader を呼ぶ
            )

        assert result.success is False
        assert result.failed_step == "saas_reader"

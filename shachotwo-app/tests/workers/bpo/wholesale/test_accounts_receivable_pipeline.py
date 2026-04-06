"""卸売業 請求・売掛管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.accounts_receivable_pipeline import (
    CREDIT_SUSPEND_RATIO,
    CREDIT_WARNING_RATIO,
    OVERDUE_DEMAND_DAYS,
    OVERDUE_NOTIFY_DAYS,
    AccountsReceivableResult,
    run_accounts_receivable_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "closing_date": "2026-03-31",
    "sales_details": [
        {
            "sales_id": "SLS-001",
            "customer_id": "CUS-001",
            "product_id": "PRD-001",
            "quantity": 72,
            "list_price": 298,
            "sales_date": "2026-03-15",
        }
    ],
    "customer_master": [
        {
            "customer_id": "CUS-001",
            "customer_name": "田中商店",
            "closing_day": "末日",
            "payment_terms": "翌月末払い",
            "discount_rate": 0.30,
            "credit_limit": 5_000_000,
            "current_receivable": 1_500_000,
            "invoice_registration_number": "T1234567890123",
        }
    ],
    "bank_data": [
        {
            "transfer_id": "TRF-001",
            "transfer_name": "タナカシヨウテン",
            "amount": 1_500_000,
            "transfer_date": "2026-03-25",
        }
    ],
    "previous_receivables": [
        {
            "customer_id": "CUS-001",
            "amount": 1_500_000,
            "due_date": "2026-03-31",
        }
    ],
    "invoice_reg_number": "T9876543210987",
}


def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.92) -> object:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name=agent_name,
        success=success,
        result=result,
        confidence=confidence,
        cost_yen=4.0,
        duration_ms=100,
    )


# ---------------------------------------------------------------------------
# テスト 1: 6ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_success() -> None:
    """全6ステップが正常完了しAccountsReceivableResultが返る"""
    saas_result = _make_micro_output("saas_reader", {
        "sales_by_customer": {"CUS-001": {"total": 21_456}},
    })
    price_result = _make_micro_output("cost_calculator", {
        "invoices_detail": [{"customer_id": "CUS-001", "amount": 15_019}],
    })
    invoice_result = _make_micro_output("document_generator", {
        "invoices": [{"invoice_id": "INV-202603-CUS001", "customer_id": "CUS-001",
                      "total_with_tax": 16_521}],
    })
    receivable_result = _make_micro_output("rule_matcher", {
        "matched_transfers": [{"transfer_id": "TRF-001", "matched_customer": "CUS-001"}],
        "auto_cleared_amount": 1_500_000,
        "manual_match_queue": [],
        "overdue_alerts": [],
    })
    credit_result = _make_micro_output("compliance_checker", {
        "credit_alerts": [],
        "delay_score_by_customer": {},
    })
    val_result = _make_micro_output("output_validator", {"valid": True})

    with (
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_saas_reader",
              new=AsyncMock(return_value=saas_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_cost_calculator",
              new=AsyncMock(return_value=price_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_document_generator",
              new=AsyncMock(return_value=invoice_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=receivable_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_compliance_checker",
              new=AsyncMock(return_value=credit_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_accounts_receivable_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert isinstance(result, AccountsReceivableResult)
    assert result.success is True
    assert len(result.steps) == 6
    assert result.failed_step is None
    assert result.final_output["invoice_count"] == 1


# ---------------------------------------------------------------------------
# テスト 2: sales_data_reader失敗で即終了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sales_reader_failure_stops_pipeline() -> None:
    """Step1(saas_reader)失敗時にfailed_step='sales_data_reader'で終了"""
    fail_out = _make_micro_output("saas_reader", {}, success=False)

    with patch("workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_accounts_receivable_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "sales_data_reader"


# ---------------------------------------------------------------------------
# テスト 3: 与信・督促設定値の確認
# ---------------------------------------------------------------------------

def test_credit_threshold_values() -> None:
    """与信限度額チェック閾値が設計仕様通り（警告80%/停止100%）"""
    assert CREDIT_WARNING_RATIO == 0.80
    assert CREDIT_SUSPEND_RATIO == 1.00


def test_overdue_threshold_values() -> None:
    """支払遅延アラート閾値が設計仕様通り（通知7日/督促30日）"""
    assert OVERDUE_NOTIFY_DAYS == 7
    assert OVERDUE_DEMAND_DAYS == 30
    assert OVERDUE_NOTIFY_DAYS < OVERDUE_DEMAND_DAYS

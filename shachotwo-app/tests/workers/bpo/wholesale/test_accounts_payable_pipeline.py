"""卸売業 仕入・買掛管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.accounts_payable_pipeline import (
    ACHIEVEMENT_REBATE_RATE,
    EARLY_PAYMENT_REBATE_RATE,
    RECEIVING_TOLERANCE_RATIO,
    VOLUME_REBATE_TABLE,
    AccountsPayableResult,
    run_accounts_payable_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "reorder_proposals": [
        {
            "product_id": "PRD-001",
            "proposed_quantity": 1920,
            "proposed_supplier": "SUP-001",
            "estimated_cost": 422_400,
        }
    ],
    "supplier_master": [
        {
            "supplier_id": "SUP-001",
            "supplier_name": "花王株式会社",
            "closing_day": "末日",
            "payment_terms": "翌月末払い",
            "min_order_amount": 50_000,
            "rebate_conditions": {
                "type": "volume",
                "thresholds": VOLUME_REBATE_TABLE,
            },
        }
    ],
    "purchase_history": [
        {
            "supplier_id": "SUP-001",
            "year_to_date_amount": 15_000_000,  # 1,500万円 → 1.0%リベート
            "period": "2025-04_2026-03",
        }
    ],
    "delivery_data": [
        {
            "supplier_id": "SUP-001",
            "product_id": "PRD-001",
            "delivered_quantity": 1920,
            "unit_price": 220.0,
            "delivery_date": "2026-04-01",
        }
    ],
    "supplier_invoices": [
        {
            "supplier_id": "SUP-001",
            "raw_text": "花王 ワイドハイター 1920個 @220円 = 422,400円",
        }
    ],
    "current_payables": [
        {
            "supplier_id": "SUP-001",
            "amount": 300_000,
            "due_date": "2026-04-30",
        }
    ],
    "period_start": "2026-03-01",
    "period_end": "2026-03-31",
}


def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.90) -> object:
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
# テスト 1: 7ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_success() -> None:
    """全7ステップが正常完了しAccountsPayableResultが返る"""
    saas_result = _make_micro_output("saas_reader", {"purchase_orders_count": 1})
    order_result = _make_micro_output("document_generator", {
        "orders": [{"order_id": "PO-001", "supplier_id": "SUP-001", "amount": 422_400}],
    })
    diff_result = _make_micro_output("diff_detector", {
        "receiving_ok": [{"supplier_id": "SUP-001", "product_id": "PRD-001"}],
        "discrepancies": [],
    })
    match_result = _make_micro_output("rule_matcher", {
        "matched": [{"supplier_id": "SUP-001", "status": "ok"}],
        "discrepancies": [],
    })
    rebate_result = _make_micro_output("cost_calculator", {
        "total_rebate_yen": 150_000,
        "by_supplier": {"SUP-001": {"rebate_rate": 0.01, "rebate_amount": 150_000}},
    })
    payment_result = _make_micro_output("cost_calculator", {
        "payment_schedule": [{"supplier_id": "SUP-001", "amount": 572_400, "due": "2026-04-30"}],
        "early_payment_recommendations": [],
        "cashflow_forecast": {"next_30_days": -572_400},
    })
    val_result = _make_micro_output("output_validator", {"valid": True})

    with (
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_saas_reader",
              new=AsyncMock(return_value=saas_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_document_generator",
              new=AsyncMock(return_value=order_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_diff_detector",
              new=AsyncMock(return_value=diff_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_document_ocr",
              new=AsyncMock(return_value=_make_micro_output("document_ocr", {"text": ""}))),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=match_result)),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_cost_calculator",
              new=AsyncMock(side_effect=[rebate_result, payment_result])),
        patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_accounts_payable_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert isinstance(result, AccountsPayableResult)
    assert result.success is True
    assert len(result.steps) == 7
    assert result.final_output["rebate_total_yen"] == 150_000
    assert result.final_output["discrepancy_count"] == 0


# ---------------------------------------------------------------------------
# テスト 2: purchase_data_reader失敗で即終了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saas_reader_failure_stops_pipeline() -> None:
    """Step1(saas_reader)失敗時にfailed_step='purchase_data_reader'で終了"""
    fail_out = _make_micro_output("saas_reader", {}, success=False)

    with patch("workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_accounts_payable_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "purchase_data_reader"


# ---------------------------------------------------------------------------
# テスト 3: リベート計算定数の確認
# ---------------------------------------------------------------------------

def test_volume_rebate_table_ordering() -> None:
    """数量リベートテーブルが降順（大口が先）になっている"""
    amounts = [row[0] for row in VOLUME_REBATE_TABLE]
    assert amounts == sorted(amounts, reverse=True), "リベートテーブルは金額降順であること"


def test_rebate_rates_are_valid() -> None:
    """リベート率の定数が設計仕様通り"""
    assert ACHIEVEMENT_REBATE_RATE == 0.005   # 0.5%
    assert EARLY_PAYMENT_REBATE_RATE == 0.010  # 1.0%


def test_receiving_tolerance_ratio() -> None:
    """検収許容誤差が±3%"""
    assert RECEIVING_TOLERANCE_RATIO == 0.03

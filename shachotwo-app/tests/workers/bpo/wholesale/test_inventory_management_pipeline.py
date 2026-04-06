"""卸売業 在庫・倉庫管理パイプライン テスト"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.inventory_management_pipeline import (
    ABC_A_THRESHOLD,
    ABC_B_THRESHOLD,
    SAFETY_FACTOR,
    InventoryManagementResult,
    _calculate_reorder_proposals,
    run_inventory_management_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "product_master": [
        {
            "product_id": "PRD-001",
            "product_name": "花王 ワイドハイターEX 詰替 480ml",
            "cost_price": 220.0,
            "lead_time_days": 3,
        },
        {
            "product_id": "PRD-002",
            "product_name": "キュキュット 食器用洗剤 本体 240ml",
            "cost_price": 185.0,
            "lead_time_days": 5,
        },
    ],
    "inventory_data": [
        {
            "product_id": "PRD-001",
            "available_quantity": 100,
            "lot_number": "L20260201",
            "expiry_date": "2029-02-01",
        },
        {
            "product_id": "PRD-002",
            "available_quantity": 600,
            "lot_number": "L20260101",
            "expiry_date": "2026-04-10",
        },
    ],
    "sales_history": [
        {"month": "2026-02", "product_id": "PRD-001", "quantity": 300},
        {"month": "2026-01", "product_id": "PRD-001", "quantity": 280},
        {"month": "2025-12", "product_id": "PRD-001", "quantity": 320},
    ],
    "io_history": [],
    "target_date": "2026-03-28",
}


def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.90) -> object:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name=agent_name,
        success=success,
        result=result,
        confidence=confidence,
        cost_yen=3.0,
        duration_ms=80,
    )


# ---------------------------------------------------------------------------
# テスト 1: 7ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_success() -> None:
    """全7ステップが正常完了しInventoryManagementResultが返る"""
    saas_result = _make_micro_output("saas_reader", {"inventory": SAMPLE_INPUT["inventory_data"]})
    abc_result = _make_micro_output("cost_calculator", {
        "products": [
            {"product_id": "PRD-001", "abc_class": "A"},
            {"product_id": "PRD-002", "abc_class": "B"},
        ]
    })
    forecast_result = _make_micro_output("cost_calculator", {
        "by_product": {
            "PRD-001": {"daily_demand": 10.0, "demand_std": 3.0},
            "PRD-002": {"daily_demand": 5.0, "demand_std": 1.5},
        }
    })
    expiry_result = _make_micro_output("rule_matcher", {
        "expiry_alerts": [
            {"product_id": "PRD-002", "expiry_date": "2026-04-10",
             "days_until_expiry": 13, "severity": "warning"}
        ],
        "fifo_violations": [],
    })
    doc_result = _make_micro_output("document_generator", {"report_url": "gs://bucket/reports/inv.pdf"})
    val_result = _make_micro_output("output_validator", {"valid": True})

    saas_calls = iter([saas_result])

    with (
        patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_saas_reader",
              new=AsyncMock(return_value=saas_result)),
        patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_cost_calculator",
              new=AsyncMock(side_effect=[abc_result, forecast_result])),
        patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=expiry_result)),
        patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_document_generator",
              new=AsyncMock(return_value=doc_result)),
        patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_inventory_management_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert isinstance(result, InventoryManagementResult)
    assert result.success is True
    assert len(result.steps) == 7
    assert result.failed_step is None
    # 賞味期限アラートが1件（PRD-002）
    assert result.final_output.get("expiry_warning_count", 0) >= 1


# ---------------------------------------------------------------------------
# テスト 2: 安全在庫・発注点・EOQ計算の検証
# ---------------------------------------------------------------------------

def test_reorder_proposals_calculation() -> None:
    """
    安全在庫・発注点・EOQが正しく計算される。

    PRD-001: ABC=A, k=1.96, lead_time=3, daily_demand=10, demand_std=3
      safety_stock = 1.96 × √3 × 3 = 1.96 × 1.732 × 3 ≒ 10
      reorder_point = 10 × 3 + 10 = 40
      available_qty=100 > reorder_point=40 → 発注提案なし

    PRD-SHORTFALL: ABC=B, available=5 → 発注点超過 → 発注提案あり
    """
    product_master = [
        {
            "product_id": "PRD-001",
            "product_name": "テスト商品A",
            "cost_price": 220.0,
            "lead_time_days": 3,
        },
        {
            "product_id": "PRD-SHORTFALL",
            "product_name": "テスト商品B（在庫不足）",
            "cost_price": 100.0,
            "lead_time_days": 5,
        },
    ]
    demand_forecast = {
        "by_product": {
            "PRD-001": {"daily_demand": 10.0, "demand_std": 3.0},
            "PRD-SHORTFALL": {"daily_demand": 20.0, "demand_std": 5.0},
        }
    }
    abc_result = {
        "products": [
            {"product_id": "PRD-001", "abc_class": "A"},
            {"product_id": "PRD-SHORTFALL", "abc_class": "B"},
        ]
    }
    current_inventory = [
        {"product_id": "PRD-001", "available_quantity": 100},
        {"product_id": "PRD-SHORTFALL", "available_quantity": 5},  # 発注点割れ
    ]

    proposals = _calculate_reorder_proposals(
        current_inventory=current_inventory,
        demand_forecast=demand_forecast,
        abc_result=abc_result,
        product_master=product_master,
    )

    # PRD-001は在庫十分なので提案なし
    prd001_proposals = [p for p in proposals if p["product_id"] == "PRD-001"]
    assert len(prd001_proposals) == 0

    # PRD-SHORTFALLは発注提案あり
    shortfall_proposals = [p for p in proposals if p["product_id"] == "PRD-SHORTFALL"]
    assert len(shortfall_proposals) == 1
    proposal = shortfall_proposals[0]
    assert proposal["current_stock"] == 5
    assert proposal["abc_class"] == "B"
    assert proposal["safety_stock"] > 0
    assert proposal["reorder_point"] > 5  # 現在庫より大きい


# ---------------------------------------------------------------------------
# テスト 3: saas_reader失敗時にパイプラインが即終了する
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saas_reader_failure_stops_pipeline() -> None:
    """saas_readerが失敗した場合、failed_step='inventory_data_reader'で終了"""
    fail_out = _make_micro_output("saas_reader", {}, success=False, confidence=0.0)

    with patch("workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_inventory_management_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "inventory_data_reader"
    assert len(result.steps) == 1


# ---------------------------------------------------------------------------
# テスト 4: ABC閾値定数の検証
# ---------------------------------------------------------------------------

def test_abc_thresholds_are_valid() -> None:
    """ABC分類閾値が設計仕様通り（A=80%, B=95%）"""
    assert ABC_A_THRESHOLD == 0.80
    assert ABC_B_THRESHOLD == 0.95
    assert ABC_A_THRESHOLD < ABC_B_THRESHOLD


def test_safety_factors_by_abc_class() -> None:
    """ABC別安全係数がA>B>Cの順になっている"""
    assert SAFETY_FACTOR["A"] > SAFETY_FACTOR["B"] > SAFETY_FACTOR["C"]
    assert SAFETY_FACTOR["A"] == 1.96   # サービス率97.5%
    assert SAFETY_FACTOR["B"] == 1.65   # サービス率95.0%
    assert SAFETY_FACTOR["C"] == 1.28   # サービス率90.0%

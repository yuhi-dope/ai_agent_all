"""卸売業 物流・配送管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.shipping_pipeline import (
    DEFAULT_OWN_DELIVERY_COST_PER_STOP,
    DELIVERY_SIZE_RATES,
    OWN_DELIVERY_SIZE_THRESHOLD,
    ShippingResult,
    run_shipping_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "order_data": [
        {
            "order_id": "ORD-001",
            "customer_id": "CUS-001",
            "customer_name": "田中商店",
            "items": [{"product_id": "PRD-001", "quantity": 72}],
        }
    ],
    "inventory_allocation": [
        {
            "product_id": "PRD-001",
            "lot_number": "L20260201",
            "location": "A-1-03-02",
            "quantity": 72,
        }
    ],
    "delivery_addresses": [
        {
            "customer_id": "CUS-001",
            "prefecture": "東京都",
            "address": "東京都台東区浅草1-2-3",
        }
    ],
    "carrier_config": {
        "yamato": True,
        "sagawa": False,
        "japan_post": False,
    },
    "own_vehicle_available": False,
    "tracking_numbers": ["1234-5678-9012"],
    "ship_date": "2026-03-29",
}


def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.92) -> object:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name=agent_name,
        success=success,
        result=result,
        confidence=confidence,
        cost_yen=2.0,
        duration_ms=60,
    )


# ---------------------------------------------------------------------------
# テスト 1: 6ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_success() -> None:
    """全6ステップが正常完了しShippingResultが返る"""
    saas_result_1 = _make_micro_output("saas_reader", {
        "shipping_targets": SAMPLE_INPUT["order_data"],
    })
    cost_result = _make_micro_output("cost_calculator", {
        "optimized_routes": [
            {"order_id": "ORD-001", "carrier": "yamato", "size": 100, "cost_yen": 1050}
        ],
    })
    picking_result = _make_micro_output("document_generator", {
        "picking_list": [{"location": "A-1-03-02", "product_id": "PRD-001", "quantity": 72}],
        "stockout_items": [],
    })
    doc_result = _make_micro_output("document_generator", {
        "shipping_instruction": {"count": 1},
        "delivery_note": {"count": 1},
        "waybill_csv": "tracking_no,address,...",
        "tracking_numbers": ["1234-5678-9012"],
    })
    saas_result_2 = _make_micro_output("saas_reader", {
        "tracking": [{"tracking_number": "1234-5678-9012", "status": "配達完了"}],
        "delay_alerts": [],
    })
    val_result = _make_micro_output("output_validator", {"valid": True})

    saas_call_count = 0

    async def mock_saas_reader(inp):
        nonlocal saas_call_count
        saas_call_count += 1
        if saas_call_count == 1:
            return saas_result_1
        return saas_result_2

    doc_call_count = 0

    async def mock_doc_generator(inp):
        nonlocal doc_call_count
        doc_call_count += 1
        if doc_call_count == 1:
            return picking_result
        return doc_result

    with (
        patch("workers.bpo.wholesale.pipelines.shipping_pipeline.run_saas_reader",
              new=AsyncMock(side_effect=mock_saas_reader)),
        patch("workers.bpo.wholesale.pipelines.shipping_pipeline.run_cost_calculator",
              new=AsyncMock(return_value=cost_result)),
        patch("workers.bpo.wholesale.pipelines.shipping_pipeline.run_document_generator",
              new=AsyncMock(side_effect=mock_doc_generator)),
        patch("workers.bpo.wholesale.pipelines.shipping_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_shipping_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert isinstance(result, ShippingResult)
    assert result.success is True
    assert len(result.steps) == 6
    assert result.failed_step is None
    assert result.final_output["total_shipments"] == 1
    assert result.final_output["delay_count"] == 0


# ---------------------------------------------------------------------------
# テスト 2: order_data_reader失敗で即終了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saas_reader_failure_stops_pipeline() -> None:
    """Step1失敗時にfailed_step='order_data_reader'で終了"""
    fail_out = _make_micro_output("saas_reader", {}, success=False)

    with patch("workers.bpo.wholesale.pipelines.shipping_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_shipping_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "order_data_reader"


# ---------------------------------------------------------------------------
# テスト 3: 宅配便サイズ料金テーブルの検証
# ---------------------------------------------------------------------------

def test_delivery_size_rates_ordered() -> None:
    """宅配便サイズ料金テーブルがサイズ昇順・料金昇順になっている"""
    sizes = list(DELIVERY_SIZE_RATES.keys())
    rates = list(DELIVERY_SIZE_RATES.values())
    assert sizes == sorted(sizes), "サイズが昇順でない"
    assert rates == sorted(rates), "料金が昇順でない"


def test_own_delivery_threshold_value() -> None:
    """自社便が有利になるサイズ閾値が設計仕様通り（120サイズ以下は宅配便）"""
    assert OWN_DELIVERY_SIZE_THRESHOLD == 120


def test_default_own_delivery_cost() -> None:
    """自社便デフォルトコストが正の値"""
    assert DEFAULT_OWN_DELIVERY_COST_PER_STOP > 0

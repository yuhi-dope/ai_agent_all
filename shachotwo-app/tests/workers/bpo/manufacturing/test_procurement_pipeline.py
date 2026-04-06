"""製造業 仕入管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.procurement_pipeline import (
    ProcurementResult,
    run_procurement_pipeline,
    _calculate_mrp,
    SUBCONTRACT_PAYMENT_MAX_DAYS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-proc"

DIRECT_INPUT = {
    "production_order": {
        "product_code": "FIN-001",
        "quantity": 10,
        "required_date": "2026-05-01",
    },
    "bom": [
        {
            "part_code": "MAT-001",
            "part_name": "SUS304丸棒",
            "quantity_per_unit": 2.0,
            "unit": "kg",
            "current_stock": 15.0,
            "pending_orders": 5.0,
            "preferred_supplier": "株式会社鋼材商",
            "unit_price": 1200.0,
            "lead_time_days": 14,
            "payment_terms_days": 30,
        },
        {
            "part_code": "BOLT-001",
            "part_name": "M8ボルト",
            "quantity_per_unit": 4.0,
            "unit": "個",
            "current_stock": 100.0,
            "pending_orders": 0.0,
            "preferred_supplier": "ファスナー工業",
            "unit_price": 50.0,
            "lead_time_days": 3,
            "payment_terms_days": 30,
        },
    ],
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={"production_order": DIRECT_INPUT["production_order"], "bom": DIRECT_INPUT["bom"]},
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_CALCULATOR_OUTPUT = MicroAgentOutput(
    agent_name="cost_calculator",
    success=True,
    result={},  # 空の場合はフォールバック計算
    confidence=0.9,
    cost_yen=1.0,
    duration_ms=80,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"selected_suppliers": []},
    confidence=0.88,
    cost_yen=1.0,
    duration_ms=60,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "発注書テスト", "format": "pdf"},
    confidence=0.85,
    cost_yen=5.0,
    duration_ms=200,
)

MOCK_VALIDATOR_OUTPUT = MicroAgentOutput(
    agent_name="output_validator",
    success=True,
    result={"valid": True},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=30,
)


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """正常入力で全7ステップが完了しProcurementResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_procurement_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, ProcurementResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: MRP計算の正確性
# ---------------------------------------------------------------------------

def test_mrp_calculation_net_requirement():
    """MRP計算で純所要量が正しく計算される"""
    production_order = {"product_code": "FIN-001", "quantity": 10}
    bom = [
        {
            "part_code": "MAT-001",
            "part_name": "テスト部品",
            "quantity_per_unit": 2.0,
            "current_stock": 5.0,
            "pending_orders": 3.0,
            "unit_price": 1000.0,
            "lead_time_days": 14,
            "payment_terms_days": 30,
        }
    ]

    result = _calculate_mrp(production_order, bom)

    # 総所要量 = 2.0 × 10 = 20
    # 純所要量 = 20 - 5(在庫) - 3(発注残) = 12
    order_reqs = result["order_requirements"]
    assert len(order_reqs) == 1
    assert order_reqs[0]["gross_requirement"] == 20.0
    assert order_reqs[0]["net_requirement"] == 12.0
    assert result["total_order_amount"] == 12.0 * 1000.0


def test_mrp_no_order_when_stock_sufficient():
    """在庫が十分な場合は発注が生成されない"""
    production_order = {"product_code": "FIN-001", "quantity": 5}
    bom = [
        {
            "part_code": "MAT-001",
            "part_name": "十分在庫品",
            "quantity_per_unit": 1.0,
            "current_stock": 100.0,  # 十分な在庫
            "pending_orders": 0.0,
            "unit_price": 1000.0,
            "lead_time_days": 14,
            "payment_terms_days": 30,
        }
    ]

    result = _calculate_mrp(production_order, bom)

    # 純所要量 = 5 - 100 = -95 → max(0, -95) = 0 なので発注なし
    assert len(result["order_requirements"]) == 0
    assert result["total_order_amount"] == 0


# ---------------------------------------------------------------------------
# テスト 3: 下請法チェック（支払期日60日超え）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subcontract_payment_violation_detected():
    """支払期日60日超えの場合にcompliance_warningsが生成される"""
    input_violation = {
        "production_order": {"product_code": "FIN-001", "quantity": 5},
        "bom": [
            {
                "part_code": "MAT-VIO",
                "part_name": "違反部品",
                "quantity_per_unit": 1.0,
                "current_stock": 0.0,
                "pending_orders": 0.0,
                "unit_price": 5000.0,
                "lead_time_days": 14,
                "payment_terms_days": 90,  # 60日超え
            }
        ],
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_procurement_pipeline(
            company_id=COMPANY_ID,
            input_data=input_violation,
        )

    assert result.success is True
    warnings = result.final_output.get("compliance_warnings", [])
    assert any("下請法" in w or "支払" in w for w in warnings)


# ---------------------------------------------------------------------------
# テスト 4: extractor失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_failure_stops_pipeline():
    """Step1失敗でパイプラインが中断される"""
    with patch(
        "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_structured_extractor",
        new=AsyncMock(return_value=MicroAgentOutput(
            agent_name="structured_extractor",
            success=False,
            result={"error": "抽出失敗"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=10,
        )),
    ):
        result = await run_procurement_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "extractor"


# ---------------------------------------------------------------------------
# テスト 5: BOMが空の場合でも正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_bom_completes_successfully():
    """BOMが空の場合でも正常に完了する"""
    input_empty_bom = {
        "production_order": {"product_code": "FIN-001", "quantity": 1},
        "bom": [],
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MicroAgentOutput(
                agent_name="structured_extractor",
                success=True,
                result={"production_order": input_empty_bom["production_order"], "bom": []},
                confidence=0.9,
                cost_yen=1.0,
                duration_ms=50,
            )),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.procurement_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_procurement_pipeline(
            company_id=COMPANY_ID,
            input_data=input_empty_bom,
        )

    assert result.success is True
    assert result.final_output.get("order_requirements", []) == []
    assert result.final_output.get("total_order_amount", 0) == 0

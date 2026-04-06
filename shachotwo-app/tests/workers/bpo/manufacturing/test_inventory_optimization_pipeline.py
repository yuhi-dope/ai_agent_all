"""製造業 在庫最適化パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline import (
    InventoryOptimizationResult,
    run_inventory_optimization_pipeline,
    _calculate_inventory_optimization,
    ABC_A_THRESHOLD,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-inv"

DIRECT_INPUT = {
    "items": [
        {
            "item_code": "MAT-001",
            "item_name": "SUS304丸棒",
            "current_stock": 50.0,
            "unit_price": 1200.0,
            "lead_time_days": 14,
            "usage_history": [8.0, 10.0, 9.0, 11.0, 8.0, 10.0,
                              9.0, 8.0, 10.0, 11.0, 9.0, 10.0],
        },
        {
            "item_code": "MAT-002",
            "item_name": "SS400フラットバー",
            "current_stock": 200.0,
            "unit_price": 400.0,
            "lead_time_days": 7,
            "usage_history": [30.0, 25.0, 35.0, 28.0, 32.0, 29.0,
                              31.0, 27.0, 33.0, 30.0, 28.0, 31.0],
        },
    ]
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={"items": DIRECT_INPUT["items"]},
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
    result={"matched": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "発注推奨リストテスト"},
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
    """正常入力で全7ステップが完了しInventoryOptimizationResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_inventory_optimization_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, InventoryOptimizationResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: ABC分析の検証
# ---------------------------------------------------------------------------

def test_abc_analysis_classification():
    """高額品目がAクラスに分類される"""
    items = [
        {
            "item_code": "A001",
            "item_name": "高額品",
            "current_stock": 10.0,
            "unit_price": 50000.0,
            "lead_time_days": 30,
            "usage_history": [5.0] * 12,
        },
        {
            "item_code": "B001",
            "item_name": "中額品",
            "current_stock": 100.0,
            "unit_price": 500.0,
            "lead_time_days": 7,
            "usage_history": [20.0] * 12,
        },
        {
            "item_code": "C001",
            "item_name": "低額品",
            "current_stock": 1000.0,
            "unit_price": 10.0,
            "lead_time_days": 3,
            "usage_history": [50.0] * 12,
        },
    ]

    result = _calculate_inventory_optimization(items)

    assert "analyzed_items" in result
    assert "abc_analysis" in result

    # 高額品目はAクラスになるはず
    high_value = next(i for i in result["analyzed_items"] if i["item_code"] == "A001")
    assert high_value["abc_class"] == "A"

    # ABC合計が品目数と一致
    abc = result["abc_analysis"]
    assert abc["a_count"] + abc["b_count"] + abc["c_count"] == len(items)


# ---------------------------------------------------------------------------
# テスト 3: 安全在庫と発注点の計算
# ---------------------------------------------------------------------------

def test_safety_stock_and_reorder_point():
    """安全在庫・発注点が正の値で計算される"""
    items = [
        {
            "item_code": "T001",
            "item_name": "テスト品",
            "current_stock": 100.0,
            "unit_price": 1000.0,
            "lead_time_days": 14,
            "usage_history": [10.0, 12.0, 8.0, 11.0, 9.0, 10.0,
                              11.0, 9.0, 10.0, 12.0, 9.0, 11.0],
        }
    ]

    result = _calculate_inventory_optimization(items)
    item = result["analyzed_items"][0]

    assert item["safety_stock"] >= 0
    assert item["reorder_point"] > 0
    assert item["recommended_order_qty"] > 0
    assert item["avg_monthly_usage"] > 0


# ---------------------------------------------------------------------------
# テスト 4: 在庫が発注点以下の場合にorder_alertsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_stock_generates_order_alert():
    """現在庫が発注点以下の場合にorder_alertsが生成される"""
    input_low_stock = {
        "items": [
            {
                "item_code": "LOW-001",
                "item_name": "在庫不足品",
                "current_stock": 1.0,   # 非常に少ない
                "unit_price": 1000.0,
                "lead_time_days": 14,
                "usage_history": [20.0] * 12,  # 使用量大
            }
        ]
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_inventory_optimization_pipeline(
            company_id=COMPANY_ID,
            input_data=input_low_stock,
        )

    assert result.success is True
    # フォールバック計算でorder_alertsが生成されているはず
    order_alerts = result.final_output.get("order_alerts", [])
    assert len(order_alerts) > 0


# ---------------------------------------------------------------------------
# テスト 5: saas_reader失敗時のフォールバック（items=[]）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_items_returns_empty_result():
    """品目リストが空の場合でも正常に完了する"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MicroAgentOutput(
                agent_name="structured_extractor",
                success=True,
                result={"items": []},
                confidence=0.9,
                cost_yen=1.0,
                duration_ms=50,
            )),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_inventory_optimization_pipeline(
            company_id=COMPANY_ID,
            input_data={"items": []},
        )

    assert result.success is True
    assert result.final_output.get("order_alerts", []) == []

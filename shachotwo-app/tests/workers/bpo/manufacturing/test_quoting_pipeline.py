"""製造業 見積AIパイプライン テスト"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.quoting_pipeline import (
    MACHINING_HOURLY_RATES,
    PROFIT_RATES,
    QuotingPipeline,
    QuotingPipelineResult,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

DIRECT_INPUT_STANDARD = {
    "product_name": "ステンレス角フランジ",
    "material": "SUS304",
    "quantity": 10,
    "processes": [
        {
            "process_name": "旋盤加工",
            "estimated_hours": 2.5,
            "setup_hours": 0.5,
        },
        {
            "process_name": "研削加工",
            "estimated_hours": 1.0,
            "setup_hours": 0.25,
        },
    ],
    "material_weight_kg": 2.5,
    "material_unit_price": 1_200,
    "order_type": "standard",
    "delivery_days": 14,
}


@pytest.fixture
def pipeline() -> QuotingPipeline:
    return QuotingPipeline()


# ---------------------------------------------------------------------------
# テスト 1: 直渡しで全 4 ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_spec_success(pipeline: QuotingPipeline) -> None:
    """直渡し入力で全 4 ステップが正常完了し QuotingPipelineResult が返る"""
    result = await pipeline.run(DIRECT_INPUT_STANDARD)

    assert isinstance(result, QuotingPipelineResult)
    assert result.product_name == "ステンレス角フランジ"
    assert result.material == "SUS304"
    assert result.quantity == 10
    assert result.total_amount > 0
    assert len(result.warnings) == 0


# ---------------------------------------------------------------------------
# テスト 2: 原価積み上げの計算正確性
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_amount_calculation(pipeline: QuotingPipeline) -> None:
    """原価積み上げ + 利益率の計算が仕様通りか検証"""
    input_data = {
        "product_name": "鉄角棒",
        "material": "SS400",
        "quantity": 5,
        "processes": [
            {
                "process_name": "フライス加工",
                "estimated_hours": 2.0,
                "setup_hours": 1.0,
            },
        ],
        "material_weight_kg": 3.0,
        "material_unit_price": 500,
        "order_type": "standard",
    }

    result = await pipeline.run(input_data)

    # 手計算
    qty = 5
    material_cost = int(3.0 * 500 * qty)                   # 7,500
    hourly_rate = MACHINING_HOURLY_RATES["フライス加工"]     # 9,000
    processing_cost = int(hourly_rate * (2.0 + 1.0) * qty)  # 135,000
    cost_subtotal = material_cost + processing_cost          # 142,500
    profit_rate = Decimal(str(PROFIT_RATES["standard"]))     # 0.25
    expected_total = int(Decimal(str(cost_subtotal)) / (Decimal("1") - profit_rate))

    assert result.total_material_cost == material_cost
    assert result.total_processing_cost == processing_cost
    assert result.total_amount == expected_total


# ---------------------------------------------------------------------------
# テスト 3: 急ぎ案件は標準より高い
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rush_order_higher_price(pipeline: QuotingPipeline) -> None:
    """order_type=rush の見積金額は standard より高い"""
    base = {
        "product_name": "テスト部品",
        "material": "SS400",
        "quantity": 1,
        "processes": [
            {"process_name": "旋盤加工", "estimated_hours": 1.0, "setup_hours": 0.5},
        ],
        "material_weight_kg": 1.0,
        "material_unit_price": 1_000,
    }

    standard_result = await pipeline.run({**base, "order_type": "standard"})
    rush_result = await pipeline.run({**base, "order_type": "rush"})

    assert rush_result.total_amount > standard_result.total_amount


# ---------------------------------------------------------------------------
# テスト 4: 材料費が正しく含まれる
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_material_cost_included(pipeline: QuotingPipeline) -> None:
    """材料費が total_material_cost に正確に反映される"""
    input_data = {
        "product_name": "アルミ板",
        "material": "A5052",
        "quantity": 20,
        "processes": [],
        "material_weight_kg": 1.5,
        "material_unit_price": 800,
        "order_type": "standard",
    }

    result = await pipeline.run(input_data)

    expected_material_cost = int(1.5 * 800 * 20)  # 24,000
    assert result.total_material_cost == expected_material_cost


# ---------------------------------------------------------------------------
# テスト 5: 全 4 ステップが実行されたことを確認
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_4_steps_executed(pipeline: QuotingPipeline) -> None:
    """steps_executed に全 4 ステップが含まれる"""
    result = await pipeline.run(DIRECT_INPUT_STANDARD)

    expected_steps = [
        "spec_reader",
        "process_estimator",
        "price_calculator",
        "output_validator",
    ]
    assert result.steps_executed == expected_steps


# ---------------------------------------------------------------------------
# テスト 6: 試作が最高利益率
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prototype_highest_margin(pipeline: QuotingPipeline) -> None:
    """order_type=prototype の見積金額は全注文種別の中で最高"""
    base = {
        "product_name": "試作部品",
        "material": "SUS316",
        "quantity": 1,
        "processes": [
            {"process_name": "旋盤加工", "estimated_hours": 1.0, "setup_hours": 0.5},
        ],
        "material_weight_kg": 1.0,
        "material_unit_price": 2_000,
    }

    results = {}
    for order_type in PROFIT_RATES:
        r = await pipeline.run({**base, "order_type": order_type})
        results[order_type] = r.total_amount

    assert results["prototype"] == max(results.values())


# ---------------------------------------------------------------------------
# テスト 7: 大量発注が最低利益率
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_lot_lowest_margin(pipeline: QuotingPipeline) -> None:
    """order_type=large_lot の見積金額は全注文種別の中で最低"""
    base = {
        "product_name": "量産部品",
        "material": "SS400",
        "quantity": 100,
        "processes": [
            {"process_name": "プレス加工", "estimated_hours": 0.5, "setup_hours": 0.1},
        ],
        "material_weight_kg": 0.5,
        "material_unit_price": 400,
    }

    results = {}
    for order_type in PROFIT_RATES:
        r = await pipeline.run({**base, "order_type": order_type})
        results[order_type] = r.total_amount

    assert results["large_lot"] == min(results.values())


# ---------------------------------------------------------------------------
# テスト 8: テキスト入力で extractor が呼び出される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_input_calls_extractor(pipeline: QuotingPipeline) -> None:
    """text キーを含む入力で _run_structured_extractor が呼び出される"""
    extracted_spec = {
        "product_name": "テキスト抽出部品",
        "material": "SUS304",
        "quantity": 3,
        "processes": [
            {"process_name": "旋盤加工", "estimated_hours": 1.0, "setup_hours": 0.5},
        ],
        "material_weight_kg": 1.0,
        "material_unit_price": 1_200,
        "order_type": "standard",
        "confidence": 0.8,
    }

    with patch.object(
        pipeline,
        "_run_structured_extractor",
        new=AsyncMock(return_value=extracted_spec),
    ) as mock_extractor:
        result = await pipeline.run({"text": "ステンレス丸棒 SUS304 数量3個 旋盤加工"})

    mock_extractor.assert_called_once()
    assert isinstance(result, QuotingPipelineResult)
    assert result.product_name == "テキスト抽出部品"
    assert result.total_amount > 0

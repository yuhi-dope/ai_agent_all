"""製造業 生産計画パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.production_planning_pipeline import (
    ProductionPlanResult,
    run_production_planning_pipeline,
    _serialize_orders,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-mfg"

DIRECT_INPUT = {
    "orders": [
        {
            "product_name": "ステンレス角フランジ",
            "quantity": 10,
            "delivery_date": "2026-04-30",
            "processes": [
                {"process_name": "旋盤加工", "estimated_hours": 2.5},
                {"process_name": "研磨加工", "estimated_hours": 1.0},
            ],
        }
    ],
    "start_date": "2026-04-01",
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={"orders": DIRECT_INPUT["orders"], "start_date": "2026-04-01"},
    confidence=0.9,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"matched_rules": [], "unmatched": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_CALCULATOR_OUTPUT = MicroAgentOutput(
    agent_name="cost_calculator",
    success=True,
    result={
        "gantt": [
            {"order": "ステンレス角フランジ", "process": "旋盤加工", "start": "2026-04-01", "end": "2026-04-03"},
        ],
        "overloaded_processes": [],
    },
    confidence=0.9,
    cost_yen=1.0,
    duration_ms=80,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "生産計画書テスト", "format": "pdf"},
    confidence=0.85,
    cost_yen=5.0,
    duration_ms=200,
)

MOCK_VALIDATOR_OUTPUT = MicroAgentOutput(
    agent_name="output_validator",
    success=True,
    result={"valid": True, "issues": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=30,
)


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """直渡し入力で全7ステップが正常完了しProductionPlanResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, ProductionPlanResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7
    assert result.total_cost_yen >= 0
    assert result.total_duration_ms >= 0


# ---------------------------------------------------------------------------
# テスト 2: ステップ番号と名前の確認
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_names_and_numbers():
    """7ステップの番号と名前が仕様通りか確認"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "rule_matcher" in step_names
    assert "calculator" in step_names
    assert "compliance" in step_names
    assert "generator" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names

    step_numbers = [s.step_no for s in result.steps]
    assert step_numbers == sorted(step_numbers)


# ---------------------------------------------------------------------------
# テスト 3: Step1（extractor）失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_failure_stops_pipeline():
    """Step1失敗時にパイプラインが中断され failed_step が返る"""
    failed_output = MicroAgentOutput(
        agent_name="structured_extractor",
        success=False,
        result={"error": "抽出エラー"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=10,
    )

    with patch(
        "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
        new=AsyncMock(return_value=failed_output),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "extractor"
    assert len(result.steps) == 1


# ---------------------------------------------------------------------------
# テスト 4: 設備稼働率超過時のアラート
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_capacity_overload_generates_alert():
    """稼働率100%超の工程がある場合にcapacity_alertsが生成される"""
    calc_with_overload = MicroAgentOutput(
        agent_name="cost_calculator",
        success=True,
        result={
            "gantt": [],
            "overloaded_processes": [
                {"process_name": "旋盤加工", "load_rate": 1.3},
            ],
        },
        confidence=0.9,
        cost_yen=1.0,
        duration_ms=80,
    )

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=calc_with_overload),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is True
    alerts = result.final_output.get("capacity_alerts", [])
    assert len(alerts) > 0
    assert "旋盤加工" in alerts[0]


# ---------------------------------------------------------------------------
# テスト 5: 納期未設定の場合にdelivery_warningsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_delivery_date_generates_warning():
    """納期が未設定の受注がある場合にdelivery_warningsが生成される"""
    input_without_delivery = {
        "orders": [
            {
                "product_name": "テスト部品",
                "quantity": 5,
                # delivery_dateなし
                "processes": [],
            }
        ],
        "start_date": "2026-04-01",
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=input_without_delivery,
        )

    assert result.success is True
    warnings = result.final_output.get("delivery_warnings", [])
    assert len(warnings) > 0


# ---------------------------------------------------------------------------
# テスト 6: コスト集計
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_cost_is_sum_of_steps():
    """total_cost_yen が各ステップコストの合計と一致する"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.production_planning_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_production_planning_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected_total = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected_total) < 0.01

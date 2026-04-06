"""製造業 設備保全パイプライン テスト"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline import (
    EquipmentMaintenanceResult,
    run_equipment_maintenance_pipeline,
    _calculate_mtbf_mttr,
    MAINTENANCE_ALERT_DAYS,
    MANDATORY_INSPECTION_ALERT_DAYS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-eqm"
TODAY = date.today()

DIRECT_INPUT = {
    "equipments": [
        {
            "equipment_id": "EQ-001",
            "equipment_name": "CNC旋盤#1",
            "equipment_type": "加工機",
            "last_maintenance_date": (TODAY - timedelta(days=80)).isoformat(),
            "maintenance_interval_days": 90,
            "is_mandatory_inspection": False,
            "operating_hours": 1200.0,
            "failure_history": [
                {"date": "2025-10-01", "repair_hours": 4.0},
                {"date": "2025-12-15", "repair_hours": 2.5},
            ],
        },
        {
            "equipment_id": "EQ-002",
            "equipment_name": "コンプレッサー#1",
            "equipment_type": "設備機器",
            "last_maintenance_date": (TODAY - timedelta(days=360)).isoformat(),
            "maintenance_interval_days": 365,
            "is_mandatory_inspection": True,
            "operating_hours": 8760.0,
            "failure_history": [],
        },
    ]
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={"equipments": DIRECT_INPUT["equipments"]},
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
    result={"content": "月次保全カレンダーテスト"},
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
    """正常入力で全7ステップが完了しEquipmentMaintenanceResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_equipment_maintenance_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, EquipmentMaintenanceResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: MTBF/MTTR計算の検証
# ---------------------------------------------------------------------------

def test_mtbf_mttr_calculation():
    """MTBF/MTTRが正しく計算される"""
    equipments = [
        {
            "equipment_id": "EQ-001",
            "equipment_name": "テスト設備",
            "equipment_type": "加工機",
            "last_maintenance_date": "2025-12-01",
            "maintenance_interval_days": 90,
            "is_mandatory_inspection": False,
            "operating_hours": 1000.0,
            "failure_history": [
                {"date": "2025-10-01", "repair_hours": 4.0},
                {"date": "2025-11-15", "repair_hours": 2.0},
            ],
        }
    ]

    result = _calculate_mtbf_mttr(equipments, date.today())

    assert "equipment_stats" in result
    eq_stat = result["equipment_stats"][0]
    assert eq_stat["equipment_id"] == "EQ-001"
    assert eq_stat["mtbf_hours"] > 0
    assert eq_stat["mttr_hours"] > 0


def test_next_maintenance_date_calculation():
    """次回保全日が正しく計算される"""
    last_date = date(2026, 1, 1)
    interval_days = 90
    equipments = [
        {
            "equipment_id": "EQ-T",
            "equipment_name": "テスト",
            "equipment_type": "機器",
            "last_maintenance_date": last_date.isoformat(),
            "maintenance_interval_days": interval_days,
            "is_mandatory_inspection": False,
            "operating_hours": 100.0,
            "failure_history": [],
        }
    ]

    result = _calculate_mtbf_mttr(equipments, date.today())
    eq_stat = result["equipment_stats"][0]

    expected_next = (last_date + timedelta(days=interval_days)).isoformat()
    assert eq_stat["next_maintenance_date"] == expected_next


# ---------------------------------------------------------------------------
# テスト 3: 保全期限超過のアラート
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overdue_maintenance_generates_alert():
    """保全期限が過ぎている設備のアラートが生成される"""
    overdue_input = {
        "equipments": [
            {
                "equipment_id": "EQ-OVERDUE",
                "equipment_name": "期限超過設備",
                "equipment_type": "加工機",
                "last_maintenance_date": (TODAY - timedelta(days=120)).isoformat(),
                "maintenance_interval_days": 90,  # 30日超過
                "is_mandatory_inspection": False,
                "operating_hours": 500.0,
                "failure_history": [],
            }
        ]
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_equipment_maintenance_pipeline(
            company_id=COMPANY_ID,
            input_data=overdue_input,
        )

    assert result.success is True
    # フォールバック計算でoverdueアラートが生成されているはず
    alerts = result.final_output.get("maintenance_alerts", [])
    overdue_alerts = [a for a in alerts if a.get("severity") == "overdue"]
    assert len(overdue_alerts) > 0


# ---------------------------------------------------------------------------
# テスト 4: 保全情報不完全な設備のmissing_equipments検出
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incomplete_equipment_info_detected():
    """保全情報（last_maintenance_date/interval）が不完全な設備を検出する"""
    input_incomplete = {
        "equipments": [
            {
                "equipment_id": "EQ-INCOMPLETE",
                "equipment_name": "情報不完全設備",
                # last_maintenance_date なし
                # maintenance_interval_days なし
                "equipment_type": "機器",
                "operating_hours": 100.0,
                "failure_history": [],
            }
        ]
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_equipment_maintenance_pipeline(
            company_id=COMPANY_ID,
            input_data=input_incomplete,
        )

    assert result.success is True
    missing = result.final_output.get("missing_equipments", [])
    assert len(missing) > 0

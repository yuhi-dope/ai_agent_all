"""介護・福祉業 シフト・勤怠管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.shift_management_pipeline import (
    ShiftManagementResult,
    run_shift_management_pipeline,
    MAX_NIGHT_SHIFTS_PER_MONTH,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

DIRECT_INPUT = {
    "facility_type": "通所介護",
    "target_year": 2026,
    "target_month": 4,
    "staff_list": [
        {
            "staff_id": "S001",
            "staff_name": "山田太郎",
            "role": "介護職員",
            "qualification": "介護福祉士",
            "desired_holidays": ["2026-04-05", "2026-04-12"],
            "night_shift_count_this_month": 4,
        },
        {
            "staff_id": "S002",
            "staff_name": "鈴木花子",
            "role": "看護職員",
            "qualification": "准看護師",
            "desired_holidays": ["2026-04-19"],
            "night_shift_count_this_month": 3,
        },
    ],
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "facility_type": "通所介護",
        "target_year": 2026,
        "target_month": 4,
        "staff_list": DIRECT_INPUT["staff_list"],
    },
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={
        "matched_rules": ["管理者", "生活相談員"],
        "unmatched": [],
    },
    confidence=0.90,
    cost_yen=1.0,
    duration_ms=60,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "シフト表 2026年4月", "format": "excel"},
    confidence=0.88,
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

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.shift_management_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """直渡し入力で全7ステップが正常完了しShiftManagementResultが返る"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, ShiftManagementResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7
    assert result.total_cost_yen >= 0


# ---------------------------------------------------------------------------
# テスト 2: ステップ名の確認
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_names_correct():
    """7ステップの名前が仕様通りか確認"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "rule_matcher" in step_names
    assert "constraint_checker" in step_names
    assert "shift_generator" in step_names
    assert "compliance_checker" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names


# ---------------------------------------------------------------------------
# テスト 3: Step1失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_failure_stops_pipeline():
    """Step1失敗時にパイプラインが中断される"""
    failed = MicroAgentOutput(
        agent_name="structured_extractor",
        success=False,
        result={"error": "抽出エラー"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=10,
    )

    with patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=failed)):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "extractor"
    assert len(result.steps) == 1


# ---------------------------------------------------------------------------
# テスト 4: 夜勤回数超過でconstraint_violationsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_night_shift_overcount_generates_constraint_violation():
    """月間夜勤回数が上限超過のスタッフがいる場合にconstraint_violationsが生成される"""
    input_with_overcount = {
        **DIRECT_INPUT,
        "staff_list": [
            {
                "staff_id": "S003",
                "staff_name": "佐藤次郎",
                "role": "介護職員",
                "qualification": "ヘルパー2級",
                "desired_holidays": [],
                "night_shift_count_this_month": MAX_NIGHT_SHIFTS_PER_MONTH + 1,
            }
        ],
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=input_with_overcount,
        )

    violations = result.final_output.get("constraint_violations", [])
    assert len(violations) > 0
    assert "夜勤" in violations[0]


# ---------------------------------------------------------------------------
# テスト 5: 人員不足で70%減算リスクが検出される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_staffing_shortage_generates_reduction_risk():
    """人員配置基準を満たさない場合にreduction_risksが生成される"""
    rule_matcher_with_shortage = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "matched_rules": [],
            "unmatched": ["管理者", "生活相談員"],
        },
        confidence=0.90,
        cost_yen=1.0,
        duration_ms=60,
    )

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=rule_matcher_with_shortage)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is True
    risks = result.final_output.get("reduction_risks", [])
    assert len(risks) > 0
    assert "70%減算" in risks[0]


# ---------------------------------------------------------------------------
# テスト 6: コスト集計
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_cost_is_sum_of_steps():
    """total_cost_yen が各ステップコストの合計と一致する"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_shift_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected) < 0.01

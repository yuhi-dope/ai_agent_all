"""介護・福祉業 服薬管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.medication_management_pipeline import (
    MedicationManagementResult,
    run_medication_management_pipeline,
    KNOWN_INTERACTIONS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

DIRECT_INPUT = {
    "user_id": "U002",
    "user_name": "鈴木一郎",
    "prescription_date": "2026-04-01",
    "doctor_name": "田中医師",
    "medications": [
        {
            "drug_name": "アムロジピン錠5mg",
            "dosage": "1錠",
            "timing": "朝食後",
            "duration_days": 30,
            "notes": "降圧剤",
        },
        {
            "drug_name": "アスピリン腸溶錠100mg",
            "dosage": "1錠",
            "timing": "朝食後",
            "duration_days": 30,
            "notes": "抗血小板薬",
        },
    ],
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "user_id": "U002",
        "user_name": "鈴木一郎",
        "prescription_date": "2026-04-01",
        "doctor_name": "田中医師",
        "medications": DIRECT_INPUT["medications"],
    },
    confidence=0.92,
    cost_yen=2.5,
    duration_ms=120,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={
        "content": "服薬管理スケジュール 鈴木一郎 2026年4月",
        "format": "pdf",
    },
    confidence=0.88,
    cost_yen=5.0,
    duration_ms=200,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"matched_rules": [], "unmatched": []},
    confidence=0.90,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_VALIDATOR_OUTPUT = MicroAgentOutput(
    agent_name="output_validator",
    success=True,
    result={"valid": True, "issues": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=30,
)

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.medication_management_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """薬剤情報が正常に処理され全7ステップが完了する"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, MedicationManagementResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: ステップ名の確認
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_names_correct():
    """7ステップの名前が仕様通りか確認"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "schedule_generator" in step_names
    assert "interaction_checker" in step_names
    assert "rule_matcher" in step_names
    assert "compliance_checker" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names


# ---------------------------------------------------------------------------
# テスト 3: ワルファリン×アスピリンで相互作用警告が出る
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warfarin_aspirin_interaction_detected():
    """ワルファリン×アスピリンの相互作用が検出される"""
    input_with_interaction = {
        **DIRECT_INPUT,
        "medications": [
            {
                "drug_name": "ワルファリンカリウム錠1mg",
                "dosage": "2錠",
                "timing": "夕食後",
                "duration_days": 90,
                "notes": "抗凝固薬",
            },
            {
                "drug_name": "アスピリン腸溶錠100mg",
                "dosage": "1錠",
                "timing": "朝食後",
                "duration_days": 30,
                "notes": "抗血小板薬",
            },
        ],
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=input_with_interaction,
        )

    assert result.success is True
    interaction_warnings = result.final_output.get("interaction_warnings", [])
    assert len(interaction_warnings) > 0
    assert "出血リスク" in interaction_warnings[0]


# ---------------------------------------------------------------------------
# テスト 4: 注射薬でlegal_warningsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_injection_drug_generates_legal_warning():
    """注射薬は介護職員が行えないことを示すlegal_warningsが生成される"""
    input_with_injection = {
        **DIRECT_INPUT,
        "medications": [
            {
                "drug_name": "インスリン グラルギン",
                "dosage": "10単位",
                "timing": "就寝前",
                "duration_days": 30,
                "notes": "注射 糖尿病",
            },
        ],
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=input_with_injection,
        )

    assert result.success is True
    legal_warnings = result.final_output.get("legal_warnings", [])
    assert len(legal_warnings) > 0
    assert "医療行為" in legal_warnings[0]


# ---------------------------------------------------------------------------
# テスト 5: schedule_generator失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_generator_failure_stops_pipeline():
    """schedule_generator失敗でパイプラインが中断される"""
    failed_gen = MicroAgentOutput(
        agent_name="document_generator",
        success=False,
        result={"error": "テンプレートエラー"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=10,
    )

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=failed_gen)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "schedule_generator"


# ---------------------------------------------------------------------------
# テスト 6: コスト集計
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_cost_is_sum_of_steps():
    """total_cost_yen が各ステップコストの合計と一致する"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_medication_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected) < 0.01

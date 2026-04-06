"""介護・福祉業 記録・日誌AIパイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.care_record_pipeline import (
    CareRecordResult,
    run_care_record_pipeline,
    VITAL_NORMAL_RANGES,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

DIRECT_INPUT = {
    "user_id": "U001",
    "user_name": "田中花子",
    "record_date": "2026-04-01 09:30",
    "recorder_name": "山田太郎",
    "service_type": "通所介護",
    "vitals": {
        "blood_pressure_systolic": 125.0,
        "blood_pressure_diastolic": 78.0,
        "pulse": 72.0,
        "temperature": 36.5,
        "spo2": 98.0,
    },
    "care_notes": "本日は機嫌よく参加。食事は全量摂取。歩行器を使用して移動。",
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "user_id": "U001",
        "user_name": "田中花子",
        "record_date": "2026-04-01 09:30",
        "recorder_name": "山田太郎",
        "care_notes": "本日は機嫌よく参加。",
    },
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={
        "content": "S: 本日は機嫌よく参加とのこと。O: BP 125/78, P 72, T 36.5, SpO2 98%. A: バイタル安定。P: 継続観察。",
        "format": "text",
    },
    confidence=0.88,
    cost_yen=6.0,
    duration_ms=250,
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

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.care_record_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """正常なバイタルデータで全7ステップが完了する"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, CareRecordResult)
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
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "soap_generator" in step_names
    assert "anomaly_detector" in step_names
    assert "rule_matcher" in step_names
    assert "compliance_checker" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names


# ---------------------------------------------------------------------------
# テスト 3: 異常バイタルでvital_alertsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_abnormal_vitals_generate_alerts():
    """血圧が正常範囲外の場合にvital_alertsが生成される"""
    input_with_high_bp = {
        **DIRECT_INPUT,
        "vitals": {
            "blood_pressure_systolic": 165.0,  # 正常範囲140を超過
            "blood_pressure_diastolic": 100.0,  # 正常範囲90を超過
            "pulse": 72.0,
            "temperature": 36.5,
            "spo2": 98.0,
        },
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=input_with_high_bp,
        )

    assert result.success is True
    vital_alerts = result.final_output.get("vital_alerts", [])
    assert len(vital_alerts) > 0
    assert any(a["item"] == "blood_pressure_systolic" for a in vital_alerts)


# ---------------------------------------------------------------------------
# テスト 4: 前回バイタルとの比較でchange_alertsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vital_change_detection():
    """前回比20%以上の変化がある場合にchange_alertsが生成される"""
    input_with_change = {
        **DIRECT_INPUT,
        "vitals": {
            "blood_pressure_systolic": 125.0,
            "blood_pressure_diastolic": 78.0,
            "pulse": 110.0,  # 前回72から大幅変化
            "temperature": 36.5,
            "spo2": 98.0,
        },
        "previous_vitals": {
            "pulse": 72.0,
        },
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=input_with_change,
        )

    assert result.success is True
    change_alerts = result.final_output.get("change_alerts", [])
    assert len(change_alerts) > 0
    assert "pulse" in change_alerts[0]


# ---------------------------------------------------------------------------
# テスト 5: soap_generator失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_soap_generator_failure_stops_pipeline():
    """soap_generator失敗時にパイプラインが中断される"""
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
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "soap_generator"


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
        result = await run_care_record_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected) < 0.01

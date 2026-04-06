"""介護・福祉業 ケアプラン作成支援パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.care_plan_pipeline import (
    CarePlanResult,
    run_care_plan_pipeline,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

DIRECT_INPUT = {
    "user_info": {
        "user_id": "U001",
        "user_name": "田中花子",
        "care_level": 3,
        "age": 82,
        "main_disease": "脳梗塞後遺症",
        "adl_notes": "歩行に一部介助が必要。食事は自力でほぼ可能。",
        "cognitive_notes": "軽度認知症。日常会話は可能。",
    },
    "assessment_text": "アセスメント：歩行・入浴に介助が必要。在宅での生活継続を希望。",
    "target_period_months": 6,
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "user_id": "U001",
        "user_name": "田中花子",
        "care_level": 3,
        "age": 82,
        "main_disease": "脳梗塞後遺症",
        "adl_notes": "歩行に一部介助が必要。",
        "cognitive_notes": "軽度認知症。",
    },
    confidence=0.92,
    cost_yen=2.5,
    duration_ms=120,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={
        "matched_rules": ["歩行介助ニーズ", "入浴介助ニーズ"],
        "unmatched": [],
    },
    confidence=0.88,
    cost_yen=1.0,
    duration_ms=80,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={
        "content": "居宅サービス計画書 田中花子 利用者氏名 要介護状態区分 居宅サービス計画作成日 計画作成者氏名 長期目標 短期目標 サービス内容 担当者氏名 有効期間",
        "format": "pdf",
    },
    confidence=0.85,
    cost_yen=8.0,
    duration_ms=300,
)

MOCK_VALIDATOR_OUTPUT = MicroAgentOutput(
    agent_name="output_validator",
    success=True,
    result={"valid": True, "issues": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=30,
)

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.care_plan_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """直渡し入力で全7ステップが正常完了しCarePlanResultが返る"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, CarePlanResult)
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
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "needs_analyzer" in step_names
    assert "rule_matcher" in step_names
    assert "plan_generator" in step_names
    assert "compliance_checker" in step_names
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

    with patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=failed_output)):
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "extractor"
    assert len(result.steps) == 1


# ---------------------------------------------------------------------------
# テスト 4: plan_generator失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_generator_failure_stops_pipeline():
    """Step4（plan_generator）失敗時にパイプラインが中断される"""
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
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=failed_gen)),
    ):
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "plan_generator"


# ---------------------------------------------------------------------------
# テスト 5: 区分支給限度基準額が正しく設定される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limit_units_set_for_care_level():
    """要介護度3の場合、limit_units=27048が設定される"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.final_output.get("limit_units") == 27_048


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
        result = await run_care_plan_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected_total = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected_total) < 0.01

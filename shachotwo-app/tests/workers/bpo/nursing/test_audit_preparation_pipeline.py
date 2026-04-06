"""介護・福祉業 監査・実地指導準備パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.audit_preparation_pipeline import (
    AuditPreparationResult,
    run_audit_preparation_pipeline,
    REQUIRED_DOCUMENTS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

# 通所介護の必須書類から数件だけ保有済みにしておく（欠損が出るようにする）
SERVICE_TYPE = "通所介護"
ALL_REQUIRED = REQUIRED_DOCUMENTS[SERVICE_TYPE]
SOME_EXISTING = ALL_REQUIRED[:5]  # 先頭5件のみ保有済み

DIRECT_INPUT = {
    "facility_name": "デイサービスあおぞら",
    "service_type": SERVICE_TYPE,
    "inspection_date": "2026-05-15",
    "existing_documents": SOME_EXISTING,
    "last_inspection_date": "2024-06-01",
    "last_findings": ["サービス提供記録の利用者署名漏れ"],
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "facility_name": "デイサービスあおぞら",
        "service_type": SERVICE_TYPE,
        "inspection_date": "2026-05-15",
        "existing_documents": SOME_EXISTING,
    },
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "実地指導準備チェックリスト デイサービスあおぞら", "format": "pdf"},
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

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.audit_preparation_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """直渡し入力で全7ステップが正常完了しAuditPreparationResultが返る"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, AuditPreparationResult)
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
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "checklist_generator" in step_names
    assert "document_scanner" in step_names
    assert "rule_matcher" in step_names
    assert "compliance_checker" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names


# ---------------------------------------------------------------------------
# テスト 3: 書類欠損が正しく検出される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_documents_detected():
    """一部書類のみ保有の場合に欠損書類が検出される"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    missing = result.final_output.get("missing_documents", [])
    assert len(missing) > 0
    # 先頭5件以外の書類が欠損として検出されるはず
    expected_missing = ALL_REQUIRED[5:]
    assert len(missing) == len(expected_missing)


# ---------------------------------------------------------------------------
# テスト 4: completeness_rateが正しく計算される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completeness_rate_calculated():
    """書類充足率が正しく計算される"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    rate = result.final_output.get("completeness_rate", 0.0)
    expected_rate = len(SOME_EXISTING) / len(ALL_REQUIRED)
    assert abs(rate - expected_rate) < 0.01


# ---------------------------------------------------------------------------
# テスト 5: 前回指摘事項がpredicted_findingsに含まれる
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_last_findings_included_in_predictions():
    """前回の指摘事項が今回の予測指摘事項に含まれる"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    findings = result.final_output.get("predicted_findings", [])
    assert any("前回指摘" in f for f in findings)
    assert any("署名漏れ" in f for f in findings)


# ---------------------------------------------------------------------------
# テスト 6: 全書類揃っている場合にmissing_documentsが空
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_missing_documents_when_all_present():
    """全書類が揃っている場合にmissing_documentsが空になる"""
    input_all_present = {
        **DIRECT_INPUT,
        "existing_documents": ALL_REQUIRED,
        "last_findings": [],
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_audit_preparation_pipeline(
            company_id=COMPANY_ID,
            input_data=input_all_present,
        )

    assert result.success is True
    missing = result.final_output.get("missing_documents", [])
    assert len(missing) == 0
    assert abs(result.final_output.get("completeness_rate", 0.0) - 1.0) < 0.01

"""製造業 SOP管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.sop_management_pipeline import (
    SOPManagementResult,
    run_sop_management_pipeline,
    SAFETY_REQUIRED_ITEMS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-sop"

DIRECT_INPUT_TEXT = {
    "text": "旋盤加工手順: 1.材料セット 2.刃物設定 3.切削 4.寸法確認",
    "title": "旋盤加工SOP",
    "process_name": "旋盤加工",
    "department": "製造部",
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "title": "旋盤加工SOP",
        "process_name": "旋盤加工",
        "steps": [
            {"step_no": 1, "description": "材料セット", "safety_notes": ["保護具着用"]},
            {"step_no": 2, "description": "刃物設定", "safety_notes": []},
            {"step_no": 3, "description": "切削", "safety_notes": ["切粉注意"]},
            {"step_no": 4, "description": "寸法確認", "safety_notes": []},
        ],
        "materials": ["SUS304"],
        "tools": ["旋盤", "マイクロメータ"],
    },
    confidence=0.88,
    cost_yen=3.0,
    duration_ms=150,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={
        "content": "保護具の着用\n緊急時対応\n作業前確認\n作業後処置\n旋盤加工手順書",
        "version": "1.0",
        "format": "pdf",
    },
    confidence=0.85,
    cost_yen=8.0,
    duration_ms=500,
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
# テスト 1: ハッピーパス（テキスト入力）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_text_input():
    """テキスト入力で全7ステップが完了しSOPManagementResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_sop_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_TEXT,
        )

    assert isinstance(result, SOPManagementResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: 安全衛生法必須記載チェック（全項目含む場合）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compliance_pass_when_all_safety_items_present():
    """全安全衛生法必須記載事項を含む場合はcompliance_warningsが空"""
    # MOCK_GENERATOR_OUTPUTのcontentには全項目が含まれる
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_sop_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_TEXT,
        )

    assert result.success is True
    warnings = result.final_output.get("compliance_warnings", [])
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# テスト 3: 安全衛生法必須記載チェック（記載不足の場合）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compliance_warning_when_safety_items_missing():
    """安全衛生法必須記載が不足する場合にcompliance_warningsが生成される"""
    generator_without_safety = MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={
            "content": "旋盤加工の手順のみ",  # 安全衛生必須記載なし
            "version": "1.0",
        },
        confidence=0.85,
        cost_yen=8.0,
        duration_ms=500,
    )

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_document_generator",
            new=AsyncMock(return_value=generator_without_safety),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_sop_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_TEXT,
        )

    assert result.success is True
    warnings = result.final_output.get("compliance_warnings", [])
    assert len(warnings) == len(SAFETY_REQUIRED_ITEMS)


# ---------------------------------------------------------------------------
# テスト 4: 改訂モード（existing_sop_id指定）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revision_mode_sets_diff_result():
    """existing_sop_idを指定した場合にdiff_resultが設定される"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_sop_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_TEXT,
            existing_sop_id="sop-001",
        )

    assert result.success is True
    diff = result.final_output.get("diff_result", {})
    assert diff.get("has_diff") is True
    assert diff.get("existing_sop_id") == "sop-001"


# ---------------------------------------------------------------------------
# テスト 5: 新規作成モード（existing_sop_id=None）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_creation_mode_no_diff():
    """existing_sop_id未指定の場合はdiff_resultのhas_diffがFalse"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.sop_management_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_sop_management_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_TEXT,
            existing_sop_id=None,
        )

    assert result.success is True
    diff = result.final_output.get("diff_result", {})
    assert diff.get("has_diff") is False

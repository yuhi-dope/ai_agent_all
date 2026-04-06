"""製造業 ISO文書管理パイプライン テスト"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.iso_document_pipeline import (
    ISODocumentResult,
    run_iso_document_pipeline,
    ISO9001_MANDATORY_DOCUMENTS,
    ISO14001_MANDATORY_DOCUMENTS,
    DOCUMENT_EXPIRY_ALERT_DAYS,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-iso"
TODAY = date.today()

# 全必須文書が揃っている場合
COMPLETE_DOCUMENTS = [
    {
        "document_id": f"DOC-{i:03d}",
        "document_name": doc_name,
        "document_type": "手順書",
        "version": "3.0",
        "last_revised_date": (TODAY - timedelta(days=365)).isoformat(),
        "department": "品質部",
        "iso_clause": "4.4",
    }
    for i, doc_name in enumerate(ISO9001_MANDATORY_DOCUMENTS)
]

DIRECT_INPUT_COMPLETE = {"documents": COMPLETE_DOCUMENTS}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={"documents": COMPLETE_DOCUMENTS},
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"clause_coverage": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "ISO監査チェックリストテスト"},
    confidence=0.85,
    cost_yen=8.0,
    duration_ms=400,
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
# テスト 1: ハッピーパス（必須文書完備）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_complete_documents():
    """全必須文書が揃っている場合に全8ステップが完了する"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_COMPLETE,
            iso_standard="9001",
        )

    assert isinstance(result, ISODocumentResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 8  # 8ステップ（他パイプラインより1ステップ多い）


# ---------------------------------------------------------------------------
# テスト 2: 必須文書欠損の検出
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_mandatory_documents_detected():
    """必須文書が不足している場合にmissing_mandatoryが生成される"""
    input_missing = {
        "documents": [
            {
                "document_id": "DOC-001",
                "document_name": "品質マニュアル",  # 1件のみ
                "document_type": "マニュアル",
                "version": "2.0",
                "last_revised_date": TODAY.isoformat(),
                "department": "品質部",
                "iso_clause": "4.4",
            }
        ]
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=input_missing,
            iso_standard="9001",
        )

    assert result.success is True
    missing = result.final_output.get("missing_mandatory", [])
    # 品質マニュアル以外の必須文書が全て欠損
    assert len(missing) == len(ISO9001_MANDATORY_DOCUMENTS) - 1


# ---------------------------------------------------------------------------
# テスト 3: ISO 14001モードでは追加必須文書もチェック
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_iso14001_mode_includes_additional_documents():
    """iso_standard='both'の場合は14001の必須文書もチェックされる"""
    # 9001文書のみ揃えた場合
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_COMPLETE,  # 9001文書のみ
            iso_standard="both",
        )

    assert result.success is True
    missing = result.final_output.get("missing_mandatory", [])
    # 14001の必須文書が欠損リストに含まれるはず
    for doc in ISO14001_MANDATORY_DOCUMENTS:
        assert doc in missing


# ---------------------------------------------------------------------------
# テスト 4: 文書有効期限切れのアラート
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_document_generates_alert():
    """有効期限切れ文書がexpiry_alertsに含まれる"""
    input_expired = {
        "documents": [
            {
                "document_id": "DOC-EXPIRED",
                "document_name": "品質マニュアル",
                "document_type": "マニュアル",
                "version": "1.0",
                "last_revised_date": "2022-01-01",
                "expiry_date": (TODAY - timedelta(days=10)).isoformat(),  # 期限切れ
                "department": "品質部",
                "iso_clause": "4.4",
            }
        ]
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=input_expired,
        )

    assert result.success is True
    expiry_alerts = result.final_output.get("expiry_alerts", [])
    expired = [a for a in expiry_alerts if a.get("severity") == "expired"]
    assert len(expired) > 0


# ---------------------------------------------------------------------------
# テスト 5: 前回監査との差分検出（previous_audit_id指定）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_previous_audit_id_sets_diff_result():
    """previous_audit_idを指定した場合にdiff_resultが設定される"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_COMPLETE,
            previous_audit_id="AUDIT-2025-001",
        )

    assert result.success is True
    diff = result.final_output.get("diff_result", {})
    assert diff.get("has_previous_audit") is True
    assert diff.get("previous_audit_id") == "AUDIT-2025-001"


# ---------------------------------------------------------------------------
# テスト 6: saas_reader失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_count_is_8():
    """ISOパイプラインは8ステップであることを確認（他は7）"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.iso_document_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_iso_document_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT_COMPLETE,
        )

    step_numbers = [s.step_no for s in result.steps]
    assert max(step_numbers) == 8
    assert len(result.steps) == 8

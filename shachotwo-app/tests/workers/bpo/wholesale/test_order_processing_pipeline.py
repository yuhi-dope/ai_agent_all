"""卸売業 受発注AIパイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.order_processing_pipeline import (
    CONFIDENCE_HITL_THRESHOLD,
    OrderProcessingResult,
    run_order_processing_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

FAX_ORDER_INPUT = {
    "document_id": "DOC-20260328-001",
    "source_type": "fax",
    "source_identifier": "03-1234-5678",
    "received_at": "2026-03-28T09:15:00",
    "document_url": "gs://bucket/fax/20260328_091500.tiff",
    "customer_id": "CUS-001",
    "product_master": [
        {
            "product_id": "PRD-001",
            "product_code": "WH-EX-480R",
            "product_name": "花王 ワイドハイターEX パワー 詰替 480ml",
            "jan_code": "4901301295095",
            "unit_price": 298,
            "case_quantity": 24,
        }
    ],
    "inventory_data": [
        {
            "product_id": "PRD-001",
            "available_quantity": 192,
            "allocated_quantity": 48,
            "incoming_quantity": 480,
        }
    ],
    "customer_master": {
        "customer_id": "CUS-001",
        "customer_name": "田中商店",
        "credit_limit": 5_000_000,
        "current_receivable": 2_000_000,
        "closing_day": "末日",
        "payment_terms": "翌月末払い",
    },
}

EMAIL_ORDER_INPUT = {
    "document_id": "DOC-20260328-002",
    "source_type": "email",
    "source_identifier": "tanaka@example.com",
    "received_at": "2026-03-28T10:00:00",
    "raw_text": "ワイドハイター詰替 3ケース、花王キュキュット 5個 お願いします。納期は3/30希望。田中商店",
    "customer_id": "CUS-001",
    "product_master": [],
    "inventory_data": [],
    "customer_master": None,
}


# ---------------------------------------------------------------------------
# モックヘルパー
# ---------------------------------------------------------------------------

def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.92) -> object:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name=agent_name,
        success=success,
        result=result,
        confidence=confidence,
        cost_yen=5.0,
        duration_ms=120,
    )


# ---------------------------------------------------------------------------
# テスト 1: FAX受注（OCRあり）の7ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fax_order_success() -> None:
    """FAX受注で全7ステップが正常完了しOrderProcessingResultが返る"""
    ocr_result = _make_micro_output("document_ocr", {"text": "田中商店 ワイドハイター 3C"})
    extract_result = _make_micro_output("structured_extractor", {
        "customer_name": "田中商店",
        "order_date": "2026-03-28",
        "desired_delivery_date": "2026-03-30",
        "items": [
            {"line_no": 1, "raw_text": "ワイドハイター 3C",
             "product_name": "ワイドハイター", "quantity": 3, "unit": "ケース"}
        ],
        "notes": "",
    })
    match_result = _make_micro_output("rule_matcher", {
        "matched_items": [{"line_no": 1, "matched_product": {"product_id": "PRD-001"},
                           "match_confidence": 0.92}],
        "hitl_required": [],
    })
    inventory_result = _make_micro_output("cost_calculator", {
        "total_amount": 21456,
        "allocation": [{"product_id": "PRD-001", "allocated_qty": 3}],
    })
    doc_result = _make_micro_output("document_generator", {"pdf_url": "gs://bucket/conf/001.pdf"})
    val_result = _make_micro_output("output_validator", {"valid": True, "errors": []})

    with (
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_ocr",
              new=AsyncMock(return_value=ocr_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_structured_extractor",
              new=AsyncMock(return_value=extract_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=match_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_cost_calculator",
              new=AsyncMock(return_value=inventory_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_generator",
              new=AsyncMock(return_value=doc_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_order_processing_pipeline(
            company_id="test-company-001",
            input_data=FAX_ORDER_INPUT,
        )

    assert isinstance(result, OrderProcessingResult)
    assert result.success is True
    assert len(result.steps) == 7
    assert result.failed_step is None
    assert result.total_cost_yen > 0


# ---------------------------------------------------------------------------
# テスト 2: メール受注（OCRスキップ）の7ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_order_ocr_skipped() -> None:
    """メール受注でStep2（OCR）がスキップされても7ステップが記録される"""
    extract_result = _make_micro_output("structured_extractor", {
        "customer_name": "田中商店",
        "order_date": "2026-03-28",
        "desired_delivery_date": "2026-03-30",
        "items": [{"line_no": 1, "raw_text": "ワイドハイター詰替 3ケース",
                   "product_name": "ワイドハイター詰替", "quantity": 3, "unit": "ケース"}],
        "notes": "",
    })
    match_result = _make_micro_output("rule_matcher", {
        "matched_items": [],
        "hitl_required": [{"line_no": 1, "reason": "商品が見つかりません", "candidates": []}],
    })
    inventory_result = _make_micro_output("cost_calculator", {"total_amount": 0, "allocation": []})
    doc_result = _make_micro_output("document_generator", {"pdf_url": ""})
    val_result = _make_micro_output("output_validator", {"valid": True, "errors": []})

    with (
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_structured_extractor",
              new=AsyncMock(return_value=extract_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=match_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_cost_calculator",
              new=AsyncMock(return_value=inventory_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_generator",
              new=AsyncMock(return_value=doc_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_order_processing_pipeline(
            company_id="test-company-001",
            input_data=EMAIL_ORDER_INPUT,
        )

    assert result.success is True
    assert len(result.steps) == 7
    # Step2はOCRスキップのはずなので cost_yen=0
    step2 = next(s for s in result.steps if s.step_no == 2)
    assert step2.cost_yen == 0.0


# ---------------------------------------------------------------------------
# テスト 3: OCR失敗時にorder_processing_result.success=Falseになる
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ocr_failure_returns_fail() -> None:
    """OCRステップが失敗した場合、failed_step='ocr_extractor'で即終了する"""
    ocr_fail = _make_micro_output("document_ocr", {}, success=False, confidence=0.0)

    with patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_ocr",
               new=AsyncMock(return_value=ocr_fail)):
        result = await run_order_processing_pipeline(
            company_id="test-company-001",
            input_data=FAX_ORDER_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "ocr_extractor"
    assert len(result.steps) == 2  # Step1(receiver) + Step2(ocr)


# ---------------------------------------------------------------------------
# テスト 4: 与信限度額超過アラートが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_credit_limit_alert_generated() -> None:
    """与信使用率90%超の場合、validation_alertsにアラートが含まれる"""
    high_credit_input = {**FAX_ORDER_INPUT}
    high_credit_input["customer_master"] = {
        "customer_id": "CUS-001",
        "customer_name": "田中商店",
        "credit_limit": 1_000_000,    # 限度100万
        "current_receivable": 850_000, # 既に85%使用
    }

    ocr_result = _make_micro_output("document_ocr", {"text": "テスト"})
    extract_result = _make_micro_output("structured_extractor", {
        "customer_name": "田中商店",
        "order_date": "2026-03-28",
        "desired_delivery_date": "2026-03-30",
        "items": [{"line_no": 1, "raw_text": "テスト", "product_name": "テスト商品",
                   "quantity": 10, "unit": "個"}],
        "notes": "",
    })
    match_result = _make_micro_output("rule_matcher", {"matched_items": [], "hitl_required": []})
    # total_amount=200,000 → 残高合計 1,050,000 > 限度100万
    inventory_result = _make_micro_output("cost_calculator", {"total_amount": 200_000, "allocation": []})
    doc_result = _make_micro_output("document_generator", {})
    val_result = _make_micro_output("output_validator", {"valid": True, "errors": []})

    with (
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_ocr",
              new=AsyncMock(return_value=ocr_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_structured_extractor",
              new=AsyncMock(return_value=extract_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=match_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_cost_calculator",
              new=AsyncMock(return_value=inventory_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_document_generator",
              new=AsyncMock(return_value=doc_result)),
        patch("workers.bpo.wholesale.pipelines.order_processing_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_order_processing_pipeline(
            company_id="test-company-001",
            input_data=high_credit_input,
        )

    assert result.success is True
    assert len(result.final_output.get("validation_alerts", [])) > 0
    alert_text = result.final_output["validation_alerts"][0]
    assert "与信限度額" in alert_text


# ---------------------------------------------------------------------------
# テスト 5: パイプラインレジストリの確認
# ---------------------------------------------------------------------------

def test_pipeline_registry_has_all_pipelines() -> None:
    """PIPlINE_REGISTRYに全6パイプラインが登録されている"""
    from workers.bpo.wholesale.pipelines import PIPELINE_REGISTRY
    expected = {
        "order_processing",
        "inventory_management",
        "accounts_receivable",
        "accounts_payable",
        "shipping",
        "sales_intelligence",
    }
    assert expected == set(PIPELINE_REGISTRY.keys())


def test_get_pipeline_runner_returns_callable() -> None:
    """get_pipeline_runnerがcallableを返す"""
    from workers.bpo.wholesale.pipelines import get_pipeline_runner
    runner = get_pipeline_runner("order_processing")
    assert callable(runner)


def test_get_pipeline_runner_unknown_raises() -> None:
    """存在しないパイプラインIDでKeyErrorが発生する"""
    from workers.bpo.wholesale.pipelines import get_pipeline_runner
    with pytest.raises(KeyError):
        get_pipeline_runner("nonexistent_pipeline")

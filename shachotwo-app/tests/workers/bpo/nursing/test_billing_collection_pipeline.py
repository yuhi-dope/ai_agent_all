"""介護・福祉業 請求・入金管理パイプライン テスト"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.nursing.pipelines.billing_collection_pipeline import (
    BillingCollectionResult,
    run_billing_collection_pipeline,
    OVERDUE_DAYS_CRITICAL,
    OVERDUE_DAYS_WARNING,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-nursing"

# 入金済みと未入金の混在データ
TODAY = date.today()
PAST_DUE_DATE = (TODAY - timedelta(days=OVERDUE_DAYS_WARNING + 10)).isoformat()
FUTURE_DUE_DATE = (TODAY + timedelta(days=10)).isoformat()

DIRECT_INPUT = {
    "period_year": 2026,
    "period_month": 3,
    "billing_records": [
        {
            "user_id": "U001",
            "user_name": "田中花子",
            "copayment_rate": "1割",
            "income_category": "一般",
            "public_fund_type": "",
            "total_service_fee": 120_000,
            "payment_due_date": FUTURE_DUE_DATE,
            "paid_date": TODAY.isoformat(),
            "paid_amount": 12_000,
        },
        {
            "user_id": "U002",
            "user_name": "鈴木一郎",
            "copayment_rate": "2割",
            "income_category": "一般",
            "public_fund_type": "",
            "total_service_fee": 90_000,
            "payment_due_date": PAST_DUE_DATE,
            "paid_date": None,  # 未入金
            "paid_amount": 0,
        },
    ],
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "period_year": 2026,
        "period_month": 3,
        "billing_records": DIRECT_INPUT["billing_records"],
    },
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_CALCULATOR_OUTPUT = MicroAgentOutput(
    agent_name="cost_calculator",
    success=True,
    result={
        "calculated_bills": [
            {"user_id": "U001", "self_pay_amount": 12_000, "high_cost_applied": False},
            {"user_id": "U002", "self_pay_amount": 18_000, "high_cost_applied": False},
        ],
    },
    confidence=0.95,
    cost_yen=1.0,
    duration_ms=80,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"matched_rules": [], "unmatched": []},
    confidence=0.90,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "介護利用料請求書 2026年3月分", "format": "pdf"},
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

PIPELINE_MODULE = "workers.bpo.nursing.pipelines.billing_collection_pipeline"


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス（全7ステップ正常完了）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """直渡し入力で全7ステップが正常完了しBillingCollectionResultが返る"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, BillingCollectionResult)
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
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    step_names = [s.step_name for s in result.steps]
    assert "extractor" in step_names
    assert "self_pay_calculator" in step_names
    assert "public_fund_checker" in step_names
    assert "invoice_generator" in step_names
    assert "uncollected_detector" in step_names
    assert "validator" in step_names
    assert "saas_writer" in step_names


# ---------------------------------------------------------------------------
# テスト 3: 期限超過未入金の場合にuncollectedが検出される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overdue_payment_detected():
    """支払期限を超過した未入金者がuncollectedに含まれる"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    uncollected = result.final_output.get("uncollected", [])
    assert len(uncollected) > 0
    # U002（鈴木一郎）が未収金として検出されるはず
    user_ids = [u["user_id"] for u in uncollected]
    assert "U002" in user_ids
    # U001（入金済み）は含まれないはず
    assert "U001" not in user_ids


# ---------------------------------------------------------------------------
# テスト 4: 全員入金済みの場合にuncollectedが空
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_paid_no_uncollected():
    """全員入金済みの場合にuncollectedが空になる"""
    input_all_paid = {
        **DIRECT_INPUT,
        "billing_records": [
            {
                **DIRECT_INPUT["billing_records"][0],
                "paid_date": TODAY.isoformat(),
                "paid_amount": 12_000,
            },
            {
                **DIRECT_INPUT["billing_records"][1],
                "paid_date": TODAY.isoformat(),
                "paid_amount": 18_000,
            },
        ],
    }

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=input_all_paid,
        )

    assert result.success is True
    uncollected = result.final_output.get("uncollected", [])
    assert len(uncollected) == 0
    assert result.final_output.get("total_uncollected_amount", 0) == 0


# ---------------------------------------------------------------------------
# テスト 5: self_pay_calculator失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calculator_failure_stops_pipeline():
    """self_pay_calculator失敗でパイプラインが中断される"""
    failed_calc = MicroAgentOutput(
        agent_name="cost_calculator",
        success=False,
        result={"error": "計算エラー"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=10,
    )

    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=failed_calc)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "self_pay_calculator"


# ---------------------------------------------------------------------------
# テスト 6: コスト集計
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_total_cost_is_sum_of_steps():
    """total_cost_yen が各ステップコストの合計と一致する"""
    with (
        patch(f"{PIPELINE_MODULE}.run_structured_extractor", new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_cost_calculator", new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_rule_matcher", new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_document_generator", new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT)),
        patch(f"{PIPELINE_MODULE}.run_output_validator", new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT)),
    ):
        result = await run_billing_collection_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    expected = sum(s.cost_yen for s in result.steps)
    assert abs(result.total_cost_yen - expected) < 0.01

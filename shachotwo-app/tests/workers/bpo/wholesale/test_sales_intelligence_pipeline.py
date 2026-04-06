"""卸売業 営業支援（売上インテリジェンス）パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.wholesale.pipelines.sales_intelligence_pipeline import (
    ASSOCIATION_LIFT_THRESHOLD,
    RFM_RANK_A,
    RFM_RANK_B,
    RFM_RANK_C,
    RFM_WEIGHT_F,
    RFM_WEIGHT_M,
    RFM_WEIGHT_R,
    SalesIntelligenceResult,
    _build_recommendation_prompt,
    run_sales_intelligence_pipeline,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "sales_records": [
        {
            "customer_id": "CUS-001",
            "product_id": "PRD-001",
            "quantity": 72,
            "unit_price": 208.6,
            "sales_date": "2026-03-15",
        },
        {
            "customer_id": "CUS-001",
            "product_id": "PRD-002",
            "quantity": 5,
            "unit_price": 200.0,
            "sales_date": "2026-03-15",
        },
    ],
    "customer_master": [
        {
            "customer_id": "CUS-001",
            "customer_name": "田中商店",
            "business_type": "小売店",
            "sales_staff_id": "STAFF-001",
        }
    ],
    "product_master": [
        {
            "product_id": "PRD-001",
            "product_name": "花王 ワイドハイターEX 詰替 480ml",
            "cost_price": 220.0,
            "category": "日用品",
        },
        {
            "product_id": "PRD-002",
            "product_name": "キュキュット 食器用洗剤",
            "cost_price": 185.0,
            "category": "日用品",
        },
    ],
    "purchase_data": [
        {
            "product_id": "PRD-001",
            "purchase_price": 220.0,
            "purchase_date": "2026-03-01",
        }
    ],
    "analysis_period_months": 12,
    "target_date": "2026-03-28",
    "sales_staff_master": [
        {"staff_id": "STAFF-001", "name": "山田太郎", "territory": "東京都"}
    ],
}


def _make_micro_output(agent_name: str, result: dict, success: bool = True,
                       confidence: float = 0.88) -> object:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name=agent_name,
        success=success,
        result=result,
        confidence=confidence,
        cost_yen=6.0,
        duration_ms=150,
    )


# ---------------------------------------------------------------------------
# テスト 1: 6ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipeline_success() -> None:
    """全6ステップが正常完了しSalesIntelligenceResultが返る"""
    saas_result = _make_micro_output("saas_reader", {
        "sales_by_customer": {"CUS-001": {"total": 21_456}},
        "customer_purchase_patterns": [],
        "top_products": [],
        "sales_by_category": {},
    })
    customer_result = _make_micro_output("cost_calculator", {
        "rfm_by_customer": {"CUS-001": {"rfm_total": 4.3, "rank": "B"}},
        "rfm_summary": {"A": 0, "B": 1, "C": 0, "D": 0},
        "churn_risk_customers": [],
        "gross_margin_by_customer": {},
    })
    product_result = _make_micro_output("cost_calculator", {
        "cross_abc_matrix": {},
        "discontinue_candidates": [],
        "trend_analysis": {},
    })
    recommendation_result = _make_micro_output("structured_extractor", {
        "recommendations": [
            {
                "customer_id": "CUS-001",
                "action_type": "cross_sell",
                "priority": "high",
                "title": "スポンジのクロスセル提案",
                "description": "田中商店は洗剤を購入中。スポンジは未購入。",
                "recommended_products": [],
                "basis": {"lift": 2.1},
                "suggested_timing": "次回訪問時",
            }
        ],
        "seasonal_forecasts": [
            {
                "product_id": "PRD-INS-001",
                "month": "2026-04",
                "forecast_quantity": 150,
                "seasonal_index": 1.0,
                "recommendation": "4月から提案開始",
            }
        ],
        "anomaly_alerts": [],
    })
    doc_result = _make_micro_output("document_generator", {"report_url": "gs://bucket/sales.pdf"})
    val_result = _make_micro_output("output_validator", {"valid": True})

    with (
        patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_saas_reader",
              new=AsyncMock(return_value=saas_result)),
        patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_cost_calculator",
              new=AsyncMock(side_effect=[customer_result, product_result])),
        patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_structured_extractor",
              new=AsyncMock(return_value=recommendation_result)),
        patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_document_generator",
              new=AsyncMock(return_value=doc_result)),
        patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_output_validator",
              new=AsyncMock(return_value=val_result)),
    ):
        result = await run_sales_intelligence_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert isinstance(result, SalesIntelligenceResult)
    assert result.success is True
    assert len(result.steps) == 6
    assert result.failed_step is None
    assert result.final_output["recommendation_count"] == 1
    assert len(result.final_output["seasonal_forecasts"]) == 1


# ---------------------------------------------------------------------------
# テスト 2: sales_data_reader失敗で即終了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saas_reader_failure_stops_pipeline() -> None:
    """Step1失敗時にfailed_step='sales_data_reader'で終了"""
    fail_out = _make_micro_output("saas_reader", {}, success=False)

    with patch("workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_sales_intelligence_pipeline(
            company_id="test-company-001",
            input_data=SAMPLE_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "sales_data_reader"


# ---------------------------------------------------------------------------
# テスト 3: RFMスコア設定値の確認
# ---------------------------------------------------------------------------

def test_rfm_weights_sum_to_one() -> None:
    """RFM重みの合計が1.0になる"""
    total = RFM_WEIGHT_R + RFM_WEIGHT_F + RFM_WEIGHT_M
    assert abs(total - 1.0) < 1e-9, f"RFM重みの合計が1.0でない: {total}"


def test_rfm_rank_thresholds_ordered() -> None:
    """RFMランク閾値がA>B>Cの順になっている"""
    assert RFM_RANK_A > RFM_RANK_B > RFM_RANK_C


def test_rfm_rank_c_above_zero() -> None:
    """RFMランクCの閾値が正の値"""
    assert RFM_RANK_C > 0


# ---------------------------------------------------------------------------
# テスト 4: アソシエーション分析のリフト値閾値
# ---------------------------------------------------------------------------

def test_association_lift_threshold() -> None:
    """リフト値閾値が設計仕様通り（1.5超でクロスセル推奨）"""
    assert ASSOCIATION_LIFT_THRESHOLD == 1.5


# ---------------------------------------------------------------------------
# テスト 5: recommendation_promptビルダーがJSON文字列を返す
# ---------------------------------------------------------------------------

def test_build_recommendation_prompt_returns_string() -> None:
    """_build_recommendation_promptが文字列を返す"""
    prompt = _build_recommendation_prompt(
        customer_analysis={"rfm_summary": {}, "churn_risk_customers": []},
        product_analysis={"cross_abc_matrix": {}},
        analysis_dataset={"sales_by_category": {}, "top_products": [],
                          "customer_purchase_patterns": []},
    )
    assert isinstance(prompt, str)
    assert len(prompt) > 0

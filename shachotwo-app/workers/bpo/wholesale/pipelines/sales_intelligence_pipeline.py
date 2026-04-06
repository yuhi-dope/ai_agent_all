"""卸売業 営業支援（売上インテリジェンス）パイプライン

Steps:
  Step 1: sales_data_reader       売上・取引データ読み込み
  Step 2: customer_analyzer       取引先分析（RFMスコア+粗利分析+離反リスク検知）
  Step 3: product_analyzer        商品分析（売上ABC×粗利ABCクロス分析+トレンド）
  Step 4: recommendation_engine   推奨商品エンジン（アソシエーション+季節需要先行提案）
  Step 5: report_generator        営業レポート生成（担当者別KPI+得意先別アクション提案）
  Step 6: output_validator        バリデーション

RFM計算式:
  R_score: 30日以内=5, 31-60=4, 61-90=3, 91-180=2, 181日以上=1
  F_score: 月4回以上=5, 月3=4, 月2=3, 月1=2, 月1未満=1
  M_score: 100万以上=5, 50-100万=4, 20-50万=3, 5-20万=2, 5万未満=1
  RFM_total = R×0.3 + F×0.3 + M×0.4
  A≥4.5 / B≥3.5 / C≥2.5 / D<2.5

アソシエーション分析:
  リフト値 = Confidence / 商品B単独購入確率
  リフト値 > 1.5 → クロスセル推奨
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.calculator import run_cost_calculator
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# RFMスコア重み
RFM_WEIGHT_R = 0.30
RFM_WEIGHT_F = 0.30
RFM_WEIGHT_M = 0.40

# RFMランク閾値
RFM_RANK_A = 4.5
RFM_RANK_B = 3.5
RFM_RANK_C = 2.5

# Rスコア区分（最終購入からの経過日数）
R_SCORE_THRESHOLDS = [30, 60, 90, 180]

# Fスコア区分（月間購入回数）
F_SCORE_THRESHOLDS = [4, 3, 2, 1]

# Mスコア区分（月間購入金額 円）
M_SCORE_THRESHOLDS = [1_000_000, 500_000, 200_000, 50_000]

# アソシエーション分析のリフト値閾値
ASSOCIATION_LIFT_THRESHOLD = 1.5

# 滞留在庫の日数閾値（これ以上出荷なし → 廃番候補）
STAGNANT_DAYS = 180

CONFIDENCE_WARNING_THRESHOLD = 0.70


@dataclass
class StepResult:
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


@dataclass
class SalesIntelligenceResult:
    """営業支援パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 営業支援パイプライン",
            f"  ステップ: {len(self.steps)}/6",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_sales_intelligence_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> SalesIntelligenceResult:
    """
    卸売業 営業支援パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "sales_records": list[dict],    # 売上実績（明細レベル）
            "customer_master": list[dict],  # 得意先マスタ
            "product_master": list[dict],   # 商品マスタ（コスト情報含む）
            "purchase_data": list[dict],    # 仕入データ（粗利計算用）
            "analysis_period_months": int,  # 分析期間（デフォルト12ヶ月）
            "target_date": str,             # 分析基準日 (YYYY-MM-DD)
            "sales_staff_master": list[dict] | None,  # 営業担当者マスタ
        }

    Returns:
        SalesIntelligenceResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {"company_id": company_id}

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms, warning=warn,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> SalesIntelligenceResult:
        return SalesIntelligenceResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    analysis_months = input_data.get("analysis_period_months", 12)

    # ─── Step 1: sales_data_reader ───────────────────────────────────────
    # 売上・取引データの読み込みと集計
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "sales_analysis_dataset",
            "sales_records": input_data.get("sales_records", []),
            "customer_master": input_data.get("customer_master", []),
            "product_master": input_data.get("product_master", []),
            "purchase_data": input_data.get("purchase_data", []),
            "analysis_months": analysis_months,
            "target_date": input_data.get("target_date", ""),
        },
        context=context,
    ))
    _add_step(1, "sales_data_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("sales_data_reader")
    analysis_dataset = s1_out.result
    context["analysis_dataset"] = analysis_dataset

    # ─── Step 2: customer_analyzer ──────────────────────────────────────
    # 取引先分析（RFM + 粗利 + 離反リスク）
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "customer_rfm_analysis",
            "analysis_dataset": analysis_dataset,
            "rfm_config": {
                "weight_r": RFM_WEIGHT_R,
                "weight_f": RFM_WEIGHT_F,
                "weight_m": RFM_WEIGHT_M,
                "rank_a_threshold": RFM_RANK_A,
                "rank_b_threshold": RFM_RANK_B,
                "rank_c_threshold": RFM_RANK_C,
                "r_score_thresholds": R_SCORE_THRESHOLDS,
                "f_score_thresholds": F_SCORE_THRESHOLDS,
                "m_score_thresholds": M_SCORE_THRESHOLDS,
            },
            # 離反リスク: 前月比で購入頻度/金額が大幅減少
            "churn_risk_detection": True,
            # 粗利分析: 売上額 - 仕入原価
            "gross_margin_analysis": True,
            "target_date": input_data.get("target_date", ""),
        },
        context=context,
    ))
    _add_step(2, "customer_analyzer", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("customer_analyzer")
    customer_analysis = s2_out.result
    context["customer_analysis"] = customer_analysis

    # ─── Step 3: product_analyzer ────────────────────────────────────────
    # 商品分析（売上ABC × 粗利ABC クロス分析）
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "product_cross_abc_analysis",
            "analysis_dataset": analysis_dataset,
            # 売上ABCと粗利ABCのクロスマトリクス
            # 売上A×粗利A → 最重要（守る）
            # 売上A×粗利C → 値上げ交渉
            # 売上C×粗利C × 6ヶ月出荷なし → 廃番候補
            "stagnant_days_threshold": STAGNANT_DAYS,
            "cross_abc_matrix": True,
        },
        context=context,
    ))
    _add_step(3, "product_analyzer", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("product_analyzer")
    product_analysis = s3_out.result
    context["product_analysis"] = product_analysis

    # ─── Step 4: recommendation_engine ──────────────────────────────────
    # 推奨商品エンジン（LLM活用）
    # アソシエーション分析 → クロスセル
    # 季節需要予測 → 先行提案
    # 購買異常検知（いつもの注文が来ない）
    s4_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _build_recommendation_prompt(
                customer_analysis=customer_analysis,
                product_analysis=product_analysis,
                analysis_dataset=analysis_dataset,
            ),
            "schema": {
                "recommendations": "list[{customer_id: str, action_type: str, "
                                   "priority: str, title: str, description: str, "
                                   "recommended_products: list, basis: dict, "
                                   "suggested_timing: str}]",
                "seasonal_forecasts": "list[{product_id: str, month: str, "
                                      "forecast_quantity: int, seasonal_index: float, "
                                      "recommendation: str}]",
                "anomaly_alerts": "list[{customer_id: str, alert_type: str, message: str}]",
            },
            "prompt_hint": (
                "卸売業の売上データから営業推奨アクションを生成してください。"
                f"アソシエーション分析でリフト値>{ASSOCIATION_LIFT_THRESHOLD}のクロスセル、"
                "季節需要の先行提案、購買異常（いつもの注文が未着）を検知してください。"
            ),
        },
        context=context,
    ))
    _add_step(4, "recommendation_engine", "structured_extractor", s4_out)
    if not s4_out.success:
        return _fail("recommendation_engine")
    recommendations = s4_out.result
    context["recommendations"] = recommendations

    # ─── Step 5: report_generator ────────────────────────────────────────
    # 営業レポート生成（担当者別KPI + 月次会議用レポート）
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "営業支援レポート",
            "variables": {
                "customer_analysis": customer_analysis,
                "product_analysis": product_analysis,
                "recommendations": recommendations,
                "sales_staff_master": input_data.get("sales_staff_master", []),
                # 担当者別KPI: 売上達成率/粗利率/新規開拓数/訪問件数
                "kpi_breakdown": True,
                # 月次営業会議用サマリー
                "meeting_summary": True,
            },
        },
        context=context,
    ))
    _add_step(5, "report_generator", "document_generator", s5_out)
    sales_report = s5_out.result
    context["sales_report"] = sales_report

    # ─── Step 6: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "customer_analysis": customer_analysis,
                "product_analysis": product_analysis,
                "recommendations": recommendations,
                "sales_report": sales_report,
            },
            "required_fields": ["customer_analysis", "recommendations"],
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    churn_risk_customers = customer_analysis.get("churn_risk_customers", [])
    discontinue_candidates = product_analysis.get("discontinue_candidates", [])
    recommendation_list = recommendations.get("recommendations", [])
    anomaly_alerts = recommendations.get("anomaly_alerts", [])

    final_output = {
        "customer_analysis": customer_analysis,
        "product_analysis": product_analysis,
        "recommendations": recommendation_list,
        "seasonal_forecasts": recommendations.get("seasonal_forecasts", []),
        "anomaly_alerts": anomaly_alerts,
        "sales_report": sales_report,
        "churn_risk_count": len(churn_risk_customers),
        "discontinue_candidate_count": len(discontinue_candidates),
        "recommendation_count": len(recommendation_list),
        "anomaly_alert_count": len(anomaly_alerts),
    }

    return SalesIntelligenceResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _build_recommendation_prompt(
    customer_analysis: dict[str, Any],
    product_analysis: dict[str, Any],
    analysis_dataset: dict[str, Any],
) -> str:
    """推奨エンジン用のLLMプロンプト入力テキストを構築する"""
    import json
    summary = {
        "rfm_summary": customer_analysis.get("rfm_summary", {}),
        "churn_risk_customers": customer_analysis.get("churn_risk_customers", []),
        "cross_abc_matrix": product_analysis.get("cross_abc_matrix", {}),
        "sales_by_category": analysis_dataset.get("sales_by_category", {}),
        "top_products": analysis_dataset.get("top_products", [])[:20],
        "customer_purchase_patterns": analysis_dataset.get(
            "customer_purchase_patterns", []
        )[:50],
    }
    return json.dumps(summary, ensure_ascii=False)

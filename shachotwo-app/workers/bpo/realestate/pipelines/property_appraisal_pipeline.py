"""不動産業 物件査定AIパイプライン

Steps:
  Step 1: property_reader      物件情報の取得・正規化（住所ゆらぎ吸収・都道府県コード付与）
  Step 2: data_collector       外部データ収集（国交省API/路線価/地価公示/自社成約DB）
  Step 3: comparison_method    取引事例比較法（事例選定→4段階補正→加重平均）
  Step 4: income_method        収益還元法（NOI/Cap Rate。投資物件のみ）
  Step 5: cost_method          原価法（土地価格+建物再調達原価×経年減価率）
  Step 6: price_synthesizer    3手法の加重平均+信頼度スコア算出+価格帯算出
  Step 7: report_generator     査定書PDF生成（物件概要+算出過程+取引事例一覧）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.saas_reader import run_saas_reader
from workers.micro.calculator import run_cost_calculator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 物件用途別の加重比率（取引事例/収益/原価）
WEIGHT_TABLE: dict[str, tuple[float, float, float]] = {
    "自用住宅":    (0.50, 0.10, 0.40),
    "投資用一棟":  (0.25, 0.60, 0.15),
    "投資用区分":  (0.30, 0.50, 0.20),
    "土地のみ":    (0.60, 0.10, 0.30),
    "事業用":      (0.20, 0.60, 0.20),
    "default":     (0.40, 0.30, 0.30),
}

# 還元利回りの参考値（地域×物件タイプ — 中間値）
CAP_RATE_TABLE: dict[str, dict[str, float]] = {
    "東京都心": {"区分マンション": 0.040, "一棟AP": 0.048, "事務所": 0.038},
    "東京23区":  {"区分マンション": 0.048, "一棟AP": 0.060, "事務所": 0.048},
    "大阪市":    {"区分マンション": 0.053, "一棟AP": 0.065, "事務所": 0.055},
    "地方都市":  {"区分マンション": 0.075, "一棟AP": 0.085, "事務所": 0.073},
    "default":   {"default": 0.060},
}

# 構造別の再調達原価（円/㎡ — 中間値）
REBUILD_COST_PER_SQM: dict[str, int] = {
    "W":   195_000,   # 木造
    "LGS": 225_000,   # 軽量鉄骨
    "S":   285_000,   # 重量鉄骨
    "RC":  330_000,   # RC造
    "SRC": 370_000,   # SRC造
    "default": 250_000,
}

# 構造別の法定耐用年数
LEGAL_LIFE_YEARS: dict[str, int] = {
    "W": 22, "LGS": 27, "S": 34, "RC": 47, "SRC": 47, "default": 30,
}


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
class PropertyAppraisalResult:
    """物件査定パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 物件査定パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        price = self.final_output.get("appraised_price")
        if price:
            lines.append(f"  査定価格: ¥{price:,}円")
        confidence = self.final_output.get("confidence")
        if confidence is not None:
            lines.append(f"  信頼度: {confidence:.2f}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_property_appraisal_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> PropertyAppraisalResult:
    """
    物件査定AIパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "property_type": str,          # 自用住宅 / 投資用一棟 / 投資用区分 / 土地のみ / 事業用
            "transaction_type": str,       # sale / purchase / rent / investment
            "address": str,                # 所在地
            "prefecture": str,             # 都道府県
            "municipality": str,           # 市区町村
            "land_area": float,            # 土地面積㎡
            "building_area": float,        # 延床面積㎡
            "building_year": int,          # 築年（西暦）
            "structure": str,              # W / LGS / S / RC / SRC
            "floor_plan": str,             # 間取り（1LDK等）
            "nearest_station": str,        # 最寄駅
            "station_distance_min": int,   # 駅徒歩（分）
            # 投資物件の場合
            "current_rent": float,         # 現行賃料
            "vacancy_rate": float,         # 空室率（0.0-1.0）
        }

    Returns:
        PropertyAppraisalResult
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

    def _fail(step_name: str) -> PropertyAppraisalResult:
        return PropertyAppraisalResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: property_reader ────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": str(input_data),
            "schema": {
                "property_type": "str",
                "transaction_type": "str",
                "address": "str",
                "prefecture": "str",
                "municipality": "str",
                "land_area": "float",
                "building_area": "float",
                "building_year": "int",
                "structure": "str",
                "floor_plan": "str",
                "nearest_station": "str",
                "station_distance_min": "int",
            },
            "purpose": "物件情報正規化・住所ゆらぎ吸収・都道府県コード付与",
        },
        context=context,
    ))
    _add_step(1, "property_reader", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("property_reader")

    # 入力データを正規化結果で補完
    property_data = {**input_data, **s1_out.result}
    # 築年数を計算
    import datetime
    current_year = datetime.date.today().year
    building_year = int(property_data.get("building_year", current_year - 10))
    property_data["age_years"] = current_year - building_year
    context["property_data"] = property_data

    # ─── Step 2: data_collector ──────────────────────────────────────────
    s2_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_sources": ["mlit_api", "land_price", "own_transactions"],
            "prefecture": property_data.get("prefecture", ""),
            "municipality": property_data.get("municipality", ""),
            "property_type": property_data.get("property_type", ""),
            "building_year_from": building_year - 10,
            "building_year_to": building_year + 5,
        },
        context=context,
    ))
    _add_step(2, "data_collector", "saas_reader", s2_out)
    if not s2_out.success:
        return _fail("data_collector")
    comparable_transactions = s2_out.result.get("transactions", [])
    land_price_per_sqm = s2_out.result.get("land_price_per_sqm", 0)
    context["comparable_transactions"] = comparable_transactions
    context["land_price_per_sqm"] = land_price_per_sqm

    # ─── Step 3: comparison_method ───────────────────────────────────────
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "comparison_appraisal",
            "transactions": comparable_transactions,
            "target_property": property_data,
            "adjustment_rules": {
                "situation_normal": 1.00,
                "situation_urgent_sale": 0.925,
                "situation_rush_buy": 1.075,
                "time_correction_annual_rate": 0.02,
            },
        },
        context=context,
    ))
    _add_step(3, "comparison_method", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("comparison_method")
    comparison_price = s3_out.result.get("appraised_price", 0)
    comparison_count = s3_out.result.get("used_count", len(comparable_transactions))
    context["comparison_price"] = comparison_price
    context["comparison_count"] = comparison_count

    # ─── Step 4: income_method ────────────────────────────────────────────
    transaction_type = property_data.get("transaction_type", "sale")
    is_investment = transaction_type in ("investment", "rent")
    if is_investment and property_data.get("current_rent"):
        s4_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id,
            agent_name="cost_calculator",
            payload={
                "calc_type": "income_appraisal",
                "monthly_rent": float(property_data.get("current_rent", 0)),
                "vacancy_rate": float(property_data.get("vacancy_rate", 0.05)),
                "management_fee_rate": 0.05,
                "property_type": property_data.get("property_type", ""),
                "prefecture": property_data.get("prefecture", ""),
                "building_year": building_year,
                "station_distance_min": int(property_data.get("station_distance_min", 10)),
                "cap_rate_table": CAP_RATE_TABLE,
            },
            context=context,
        ))
    else:
        # 収益データなし — スキップ（コスト0のダミー出力）
        s4_start = int(time.time() * 1000)
        s4_out = MicroAgentOutput(
            agent_name="cost_calculator",
            success=True,
            result={"appraised_price": 0, "skipped": True, "reason": "非投資物件"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    _add_step(4, "income_method", "cost_calculator", s4_out)
    income_price = s4_out.result.get("appraised_price", 0)
    context["income_price"] = income_price

    # ─── Step 5: cost_method ──────────────────────────────────────────────
    structure = property_data.get("structure", "default")
    rebuild_cost = REBUILD_COST_PER_SQM.get(structure, REBUILD_COST_PER_SQM["default"])
    legal_life = LEGAL_LIFE_YEARS.get(structure, LEGAL_LIFE_YEARS["default"])
    age_years = property_data.get("age_years", 10)

    s5_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "cost_appraisal",
            "land_area": float(property_data.get("land_area", 0)),
            "building_area": float(property_data.get("building_area", 0)),
            "land_price_per_sqm": float(land_price_per_sqm),
            "rebuild_cost_per_sqm": float(rebuild_cost),
            "age_years": int(age_years),
            "legal_life_years": int(legal_life),
            "renovation_status": property_data.get("renovation_status", "none"),
            "management_condition": property_data.get("management_condition", "normal"),
        },
        context=context,
    ))
    _add_step(5, "cost_method", "cost_calculator", s5_out)
    if not s5_out.success:
        return _fail("cost_method")
    cost_price = s5_out.result.get("appraised_price", 0)
    context["cost_price"] = cost_price

    # ─── Step 6: price_synthesizer ────────────────────────────────────────
    property_type = property_data.get("property_type", "default")
    w_comparison, w_income, w_cost = WEIGHT_TABLE.get(
        property_type, WEIGHT_TABLE["default"]
    )
    # 収益データなし → 収益法ウェイトを0にして按分
    if income_price == 0:
        total_w = w_comparison + w_cost
        if total_w > 0:
            w_comparison = w_comparison / total_w
            w_cost = w_cost / total_w
        w_income = 0.0

    s6_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "price_synthesis",
            "comparison_price": float(comparison_price),
            "income_price": float(income_price),
            "cost_price": float(cost_price),
            "weights": {
                "comparison": w_comparison,
                "income": w_income,
                "cost": w_cost,
            },
            "comparable_count": int(comparison_count),
            "data_freshness_years": 1,
            "area_coverage": "within_1km" if comparison_count >= 3 else "within_5km",
        },
        context=context,
    ))
    _add_step(6, "price_synthesizer", "rule_matcher", s6_out)
    if not s6_out.success:
        return _fail("price_synthesizer")
    appraised_price = s6_out.result.get("appraised_price", 0)
    price_low = s6_out.result.get("price_range_low", int(appraised_price * 0.90))
    price_high = s6_out.result.get("price_range_high", int(appraised_price * 1.10))
    synthesis_confidence = s6_out.result.get("confidence", 0.70)
    context["appraised_price"] = appraised_price
    context["price_range"] = {"low": price_low, "high": price_high}
    context["synthesis_confidence"] = synthesis_confidence

    # ─── Step 7: report_generator ─────────────────────────────────────────
    s7_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "査定書",
            "variables": {
                "property_data": property_data,
                "comparison_price": comparison_price,
                "income_price": income_price,
                "cost_price": cost_price,
                "appraised_price": appraised_price,
                "price_range_low": price_low,
                "price_range_high": price_high,
                "confidence": synthesis_confidence,
                "comparable_transactions": comparable_transactions[:10],
                "weights": {
                    "comparison": w_comparison,
                    "income": w_income,
                    "cost": w_cost,
                },
            },
            "output_format": "pdf",
        },
        context=context,
    ))
    _add_step(7, "report_generator", "document_generator", s7_out)
    if not s7_out.success:
        logger.warning(f"[property_appraisal] 査定書生成失敗（査定価格は算出済み）")

    final_output = {
        "property_data": property_data,
        "comparison_price": comparison_price,
        "income_price": income_price,
        "cost_price": cost_price,
        "appraised_price": appraised_price,
        "price_range_low": price_low,
        "price_range_high": price_high,
        "confidence": synthesis_confidence,
        "comparable_count": comparison_count,
        "weights": {"comparison": w_comparison, "income": w_income, "cost": w_cost},
        "generated_report": s7_out.result,
    }

    return PropertyAppraisalResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

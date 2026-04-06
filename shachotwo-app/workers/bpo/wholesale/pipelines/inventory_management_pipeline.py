"""卸売業 在庫・倉庫管理パイプライン

Steps:
  Step 1: inventory_data_reader   在庫データ読み込み（商品マスタ+在庫+入出庫履歴）
  Step 2: abc_analyzer            ABC分析（年間売上→累積構成比→A/B/C分類）
  Step 3: demand_forecaster       需要予測（移動平均/指数平滑/季節指数の自動選択）
  Step 4: reorder_calculator      安全在庫・発注点・EOQ計算
  Step 5: expiry_manager          賞味期限・ロット管理（FIFO+期限切れアラート）
  Step 6: report_generator        在庫レポート生成
  Step 7: output_validator        バリデーション

計算式:
  安全在庫 = k × √(リードタイム日数) × 需要標準偏差
  発注点   = 平均日販 × リードタイム + 安全在庫
  EOQ      = √(2 × 年間需要 × 発注コスト / 保管コスト率 × 単価)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.calculator import run_cost_calculator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# ABC分類の累積構成比閾値
ABC_A_THRESHOLD = 0.80  # 〜80%: Aランク
ABC_B_THRESHOLD = 0.95  # 80-95%: Bランク（残りC）

# ABC別安全係数（サービス率）
SAFETY_FACTOR = {
    "A": 1.96,  # 97.5%
    "B": 1.65,  # 95.0%
    "C": 1.28,  # 90.0%
}

# 発注コスト（デフォルト: 円/回）
DEFAULT_ORDER_COST = 3000.0

# 年間在庫保管コスト率
DEFAULT_HOLDING_COST_RATE = 0.20  # 20%

# 賞味期限アラートの日数閾値
EXPIRY_WARNING_DAYS = 30
EXPIRY_CRITICAL_DAYS = 7

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
class InventoryManagementResult:
    """在庫・倉庫管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 在庫・倉庫管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
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


async def run_inventory_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> InventoryManagementResult:
    """
    卸売業 在庫・倉庫管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "product_master": list[dict],       # 商品マスタ
            "inventory_data": list[dict],       # 現在の在庫データ（lot/expiry含む）
            "sales_history": list[dict],        # 月次販売実績（12-24ヶ月）
            "io_history": list[dict],           # 入出庫履歴
            "target_date": str,                 # 分析基準日 (YYYY-MM-DD)
        }

    Returns:
        InventoryManagementResult
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

    def _fail(step_name: str) -> InventoryManagementResult:
        return InventoryManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    product_master = input_data.get("product_master", [])
    inventory_data = input_data.get("inventory_data", [])
    sales_history = input_data.get("sales_history", [])

    # ─── Step 1: inventory_data_reader ────────────────────────────────────
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "inventory_snapshot",
            "product_master": product_master,
            "inventory_data": inventory_data,
            "io_history": input_data.get("io_history", []),
        },
        context=context,
    ))
    _add_step(1, "inventory_data_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("inventory_data_reader")
    current_inventory = s1_out.result.get("inventory", inventory_data)
    context["current_inventory"] = current_inventory

    # ─── Step 2: abc_analyzer ────────────────────────────────────────────
    # 商品別年間売上→累積構成比→A/B/C分類
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "abc_analysis",
            "sales_history": sales_history,
            "product_master": product_master,
            "a_threshold": ABC_A_THRESHOLD,
            "b_threshold": ABC_B_THRESHOLD,
        },
        context=context,
    ))
    _add_step(2, "abc_analyzer", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("abc_analyzer")
    abc_result = s2_out.result
    context["abc_result"] = abc_result

    # ─── Step 3: demand_forecaster ───────────────────────────────────────
    # 需要予測（移動平均/指数平滑/季節指数を自動選択）
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "demand_forecast",
            "sales_history": sales_history,
            "abc_result": abc_result,
            # 変動係数 < 0.3 → 移動平均、0.3-0.7 → 指数平滑、季節品 → 季節指数法
            "forecast_months": 3,
            "smoothing_alpha": 0.3,
        },
        context=context,
    ))
    _add_step(3, "demand_forecaster", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("demand_forecaster")
    demand_forecast = s3_out.result
    context["demand_forecast"] = demand_forecast

    # ─── Step 4: reorder_calculator ──────────────────────────────────────
    # 安全在庫・発注点・EOQ計算
    s4_start = int(time.time() * 1000)
    reorder_proposals = _calculate_reorder_proposals(
        current_inventory=current_inventory,
        demand_forecast=demand_forecast,
        abc_result=abc_result,
        product_master=product_master,
    )
    s4_out = MicroAgentOutput(
        agent_name="cost_calculator",
        success=True,
        result={"reorder_proposals": reorder_proposals},
        confidence=0.92,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "reorder_calculator", "cost_calculator", s4_out)
    context["reorder_proposals"] = reorder_proposals

    # ─── Step 5: expiry_manager ───────────────────────────────────────────
    # 賞味期限・ロット管理（FIFO + 期限切れアラート）
    s5_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "expiry_check",
            "inventory_data": current_inventory,
            "warning_days": EXPIRY_WARNING_DAYS,
            "critical_days": EXPIRY_CRITICAL_DAYS,
            # FIFO出荷チェック（入荷日/ロット番号の古い順）
            "fifo_check": True,
        },
        context=context,
    ))
    _add_step(5, "expiry_manager", "rule_matcher", s5_out)
    expiry_alerts = s5_out.result.get("expiry_alerts", [])
    fifo_violations = s5_out.result.get("fifo_violations", [])
    context["expiry_alerts"] = expiry_alerts
    context["fifo_violations"] = fifo_violations

    # ─── Step 6: report_generator ────────────────────────────────────────
    # 在庫レポート生成（在庫金額/回転率/滞留在庫/欠品率）
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "在庫レポート",
            "variables": {
                "current_inventory": current_inventory,
                "abc_result": abc_result,
                "demand_forecast": demand_forecast,
                "reorder_proposals": reorder_proposals,
                "expiry_alerts": expiry_alerts,
                "fifo_violations": fifo_violations,
                # 在庫回転率 = 年間売上原価 / 平均在庫金額
                # 滞留在庫: 90日以上出荷なし
                # 欠品率: 欠品件数 / 総受注件数 × 100%
            },
        },
        context=context,
    ))
    _add_step(6, "report_generator", "document_generator", s6_out)
    context["inventory_report"] = s6_out.result

    # ─── Step 7: output_validator ─────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "abc_result": abc_result,
                "reorder_proposals": reorder_proposals,
                "expiry_alerts": expiry_alerts,
            },
            "required_fields": ["abc_result", "reorder_proposals"],
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", val_out)

    final_output = {
        "current_inventory": current_inventory,
        "abc_result": abc_result,
        "demand_forecast": demand_forecast,
        "reorder_proposals": reorder_proposals,
        "expiry_alerts": expiry_alerts,
        "fifo_violations": fifo_violations,
        "inventory_report": s6_out.result,
        "reorder_count": len([p for p in reorder_proposals if p.get("urgency")]),
        "expiry_warning_count": len(expiry_alerts),
    }

    return InventoryManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _calculate_reorder_proposals(
    current_inventory: list[dict],
    demand_forecast: dict[str, Any],
    abc_result: dict[str, Any],
    product_master: list[dict],
) -> list[dict[str, Any]]:
    """
    安全在庫・発注点・EOQを計算して発注提案リストを返す。

    安全在庫 = k × √(リードタイム日数) × 需要の標準偏差
    発注点   = 平均日販 × リードタイム + 安全在庫
    EOQ      = √(2 × 年間需要 × 発注コスト / 保管コスト率 × 単価)
    """
    forecasts: dict[str, Any] = demand_forecast.get("by_product", {})
    abc_classes: dict[str, str] = {
        item.get("product_id", ""): item.get("abc_class", "C")
        for item in abc_result.get("products", [])
    }
    proposals: list[dict[str, Any]] = []

    inventory_by_product: dict[str, dict] = {
        inv.get("product_id", ""): inv for inv in current_inventory
    }

    for product in product_master:
        pid = product.get("product_id") or product.get("id", "")
        inv = inventory_by_product.get(pid, {})
        available_qty = inv.get("available_quantity", 0)
        lead_time = product.get("lead_time_days", 3)
        cost_price = product.get("cost_price", 0.0)
        abc_class = abc_classes.get(pid, "C")
        k = SAFETY_FACTOR.get(abc_class, SAFETY_FACTOR["C"])

        # 需要予測値から日販と標準偏差を取得
        forecast = forecasts.get(pid, {})
        daily_demand = forecast.get("daily_demand", 0.0)
        demand_std = forecast.get("demand_std", daily_demand * 0.3)  # デフォルト: 変動30%
        annual_demand = daily_demand * 365

        # 安全在庫計算
        safety_stock = int(k * math.sqrt(lead_time) * demand_std) if demand_std > 0 else 0

        # 発注点計算
        reorder_point = int(daily_demand * lead_time + safety_stock)

        # EOQ計算
        eoq = 0
        if annual_demand > 0 and cost_price > 0:
            eoq_sq = (2 * annual_demand * DEFAULT_ORDER_COST) / (
                DEFAULT_HOLDING_COST_RATE * cost_price
            )
            eoq = int(math.sqrt(eoq_sq)) if eoq_sq > 0 else 0

        if available_qty <= reorder_point:
            proposals.append({
                "product_id": pid,
                "product_name": product.get("product_name", ""),
                "abc_class": abc_class,
                "current_stock": available_qty,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "eoq": eoq,
                "proposed_quantity": max(eoq, reorder_point - available_qty),
                "estimated_cost": max(eoq, reorder_point - available_qty) * cost_price,
                "reason": (
                    f"在庫({available_qty})が発注点({reorder_point})を下回りました"
                ),
                "urgency": "緊急" if available_qty <= safety_stock else "通常",
            })

    return proposals

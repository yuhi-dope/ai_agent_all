"""製造業 仕入管理パイプライン（MRP対応）

Steps:
  Step 1: extractor       BOM構造化（親品目→子部品展開）
  Step 2: calculator      所要量計算（MRP: 総所要量 - 在庫 - 発注残）
  Step 3: rule_matcher    発注先選定ルール照合（価格/納期/品質）
  Step 4: compliance      下請法チェック（支払期日60日ルール等）
  Step 5: generator       発注書PDF生成
  Step 6: validator       発注金額・納期の妥当性チェック
  Step 7: saas_writer     発注記録保存 + 仕入先通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 下請法チェック基準（支払期日）
SUBCONTRACT_PAYMENT_MAX_DAYS = 60   # 60日以内

# 発注金額妥当性チェック
UNIT_PRICE_VARIANCE_THRESHOLD = 0.30  # ±30%以上の価格差はアラート


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
class ProcurementResult:
    """仕入管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 仕入管理パイプライン",
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


async def run_procurement_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> ProcurementResult:
    """
    製造業仕入管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "production_order": {
                "product_code": str,
                "quantity": int,
                "required_date": str,  # YYYY-MM-DD
            },
            "bom": [
                {
                    "part_code": str,
                    "part_name": str,
                    "quantity_per_unit": float,
                    "unit": str,
                    "current_stock": float,
                    "pending_orders": float,    # 発注残
                    "preferred_supplier": str,
                    "unit_price": float,
                    "lead_time_days": int,
                    "payment_terms_days": int,  # 支払条件（日）
                }
            ]
        }

    Returns:
        ProcurementResult
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

    def _fail(step_name: str) -> ProcurementResult:
        return ProcurementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    production_order = input_data.get("production_order", {})
    bom: list[dict] = input_data.get("bom", [])
    context.update({"production_order": production_order, "bom": bom})

    # ─── Step 1: extractor (BOM構造化) ──────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _serialize_bom(input_data),
            "schema": {
                "production_order": "{product_code: str, quantity: int, required_date: str}",
                "bom": "list[{part_code: str, part_name: str, quantity_per_unit: float, "
                       "current_stock: float, pending_orders: float, unit_price: float, "
                       "lead_time_days: int, payment_terms_days: int}]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")

    # ─── Step 2: calculator (MRP所要量計算) ─────────────────────────────
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "mrp",
            "production_order": production_order,
            "bom": bom,
        },
        context=context,
    ))
    _add_step(2, "calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("calculator")

    # フォールバック計算
    mrp_result = s2_out.result
    if not mrp_result.get("order_requirements") and bom:
        mrp_result = _calculate_mrp(production_order, bom)
    context["mrp_result"] = mrp_result

    # ─── Step 3: rule_matcher (発注先選定) ──────────────────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": mrp_result.get("order_requirements", []),
            "rule_type": "supplier_selection",
            "criteria": ["price", "delivery", "quality"],
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    order_requirements = mrp_result.get("order_requirements", [])
    context["order_requirements"] = order_requirements

    # ─── Step 4: compliance (下請法チェック) ────────────────────────────
    s4_start = int(time.time() * 1000)
    compliance_warnings: list[str] = []
    for req in order_requirements:
        payment_terms = int(req.get("payment_terms_days", 0))
        part_name = req.get("part_name", "不明")
        if payment_terms > SUBCONTRACT_PAYMENT_MAX_DAYS:
            compliance_warnings.append(
                f"下請法違反リスク: '{part_name}' の支払条件 {payment_terms}日"
                f"（上限{SUBCONTRACT_PAYMENT_MAX_DAYS}日）"
            )

    s4_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "compliance_warnings": compliance_warnings,
            "passed": len(compliance_warnings) == 0,
            "checked_rule": f"支払期日{SUBCONTRACT_PAYMENT_MAX_DAYS}日ルール",
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance", "compliance_checker", s4_out)
    context["compliance_warnings"] = compliance_warnings

    # ─── Step 5: generator (発注書PDF生成) ──────────────────────────────
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "発注書",
            "variables": {
                "production_order": production_order,
                "order_requirements": order_requirements,
                "compliance_warnings": compliance_warnings,
                "total_amount": mrp_result.get("total_order_amount", 0),
            },
        },
        context=context,
    ))
    _add_step(5, "generator", "document_generator", s5_out)
    context["generated_doc"] = s5_out.result

    # ─── Step 6: validator (発注金額・納期チェック) ──────────────────────
    validity_warnings: list[str] = []
    for req in order_requirements:
        # 納期チェック
        required_date = production_order.get("required_date", "")
        lead_time = int(req.get("lead_time_days", 0))
        if lead_time > 0 and required_date:
            # TODO: 詳細な納期逆算チェック
            pass
        # 単価変動チェック（TODO: 過去の取引データと比較）

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "production_order": production_order,
                "order_requirements": order_requirements,
                "total_amount": mrp_result.get("total_order_amount", 0),
                "validity_warnings": validity_warnings,
            },
            "required_fields": ["production_order", "order_requirements"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: 発注記録保存（purchase_orders テーブル）+ 仕入先通知メール
    logger.info(
        f"procurement_pipeline: company_id={company_id}, "
        f"product={production_order.get('product_code')}, "
        f"order_items={len(order_requirements)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "supplier_notified": False,  # TODO: 仕入先通知実装
            "order_items_count": len(order_requirements),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "production_order": production_order,
        "order_requirements": order_requirements,
        "total_order_amount": mrp_result.get("total_order_amount", 0),
        "compliance_warnings": compliance_warnings,
        "generated_doc": s5_out.result,
    }

    return ProcurementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _calculate_mrp(
    production_order: dict[str, Any],
    bom: list[dict[str, Any]],
) -> dict[str, Any]:
    """MRP所要量計算（総所要量 - 在庫 - 発注残 = 純所要量）"""
    order_qty = int(production_order.get("quantity", 1))
    order_requirements = []
    total_order_amount = 0

    for part in bom:
        qty_per_unit = float(part.get("quantity_per_unit", 1))
        current_stock = float(part.get("current_stock", 0))
        pending_orders = float(part.get("pending_orders", 0))
        unit_price = float(part.get("unit_price", 0))

        # MRP計算
        gross_requirement = qty_per_unit * order_qty  # 総所要量
        net_requirement = max(
            0, gross_requirement - current_stock - pending_orders
        )  # 純所要量

        if net_requirement > 0:
            order_amount = net_requirement * unit_price
            total_order_amount += order_amount
            order_requirements.append({
                **part,
                "gross_requirement": round(gross_requirement, 2),
                "net_requirement": round(net_requirement, 2),
                "order_amount": round(order_amount, 0),
            })

    return {
        "order_requirements": order_requirements,
        "total_order_amount": round(total_order_amount, 0),
    }


def _serialize_bom(input_data: dict[str, Any]) -> str:
    import json
    return json.dumps(input_data, ensure_ascii=False)

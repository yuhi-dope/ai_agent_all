"""物流・運送業 請求・運賃計算パイプライン

Steps:
  Step 1: extractor           運送実績データ構造化（荷主×案件×運賃体系）
  Step 2: rate_calculator     運賃計算（距離制/重量制/個建て×荷主別契約単価）
  Step 3: surcharge_calc      燃料サーチャージ計算（基準軽油価格×変動率）
  Step 4: rule_matcher        運賃計算根拠チェック（標準的な運賃告示との照合）
  Step 5: invoice_generator   請求書PDF生成（荷主別・月次）
  Step 6: validator           請求金額整合性チェック（合計・消費税・振込先）
  Step 7: saas_writer         execution_logs保存 + 請求書送付通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 消費税率
CONSUMPTION_TAX_RATE = 0.10

# 燃料サーチャージ基準軽油価格（円/L）
BASE_DIESEL_PRICE = 90.0  # 基準価格（これを超えると加算）
SURCHARGE_PER_LITER = 1.0  # 基準超過1円につき加算額

# 運賃計算方式
RATE_TYPES = {
    "distance": "距離制",
    "weight": "重量制",
    "per_unit": "個建て",
    "time": "時間制",
    "mixed": "複合制",
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
class FreightBillingResult:
    """請求・運賃計算パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 請求・運賃計算パイプライン",
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


async def run_freight_billing_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> FreightBillingResult:
    """
    請求・運賃計算パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "billing_month": str,           # YYYY-MM
            "shipper_id": str,
            "shipper_name": str,
            "shipments": list[{
                "shipment_id": str,
                "operation_date": str,      # YYYY-MM-DD
                "origin": str,
                "destination": str,
                "distance_km": float,
                "weight_kg": float,
                "unit_count": int,
                "vehicle_type": str,
                "rate_type": str,           # distance/weight/per_unit/time/mixed
                "contracted_rate": float,   # 契約単価（円/km or 円/kg or 円/個）
            }],
            "diesel_price_yen": float,      # 当月の軽油価格（円/L）
            "bank_info": {
                "bank_name": str,
                "branch_name": str,
                "account_type": str,
                "account_no": str,
                "account_holder": str,
            },
        }

    Returns:
        FreightBillingResult
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

    def _fail(step_name: str) -> FreightBillingResult:
        return FreightBillingResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    shipments = input_data.get("shipments", [])

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "billing_month": "string",
                "shipper_name": "string",
                "shipments": "list",
                "diesel_price_yen": "float",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: rate_calculator（運賃計算）─────────────────────────────
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "freight_rate",
            "shipments": shipments,
            "rate_types": RATE_TYPES,
        },
        context=context,
    ))
    _add_step(2, "rate_calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("rate_calculator")
    base_freight_total = s2_out.result.get("total_yen", 0.0)
    shipment_details = s2_out.result.get("details", [])
    context["freight_calc"] = s2_out.result

    # ─── Step 3: surcharge_calc（燃料サーチャージ計算）──────────────────
    s3_start = int(time.time() * 1000)
    diesel_price = input_data.get("diesel_price_yen", BASE_DIESEL_PRICE)
    price_diff = max(0.0, diesel_price - BASE_DIESEL_PRICE)

    # 走行距離合計に基づくサーチャージ計算（簡易）
    total_distance = sum(s.get("distance_km", 0.0) for s in shipments)
    # TODO: 実際のサーチャージ計算式は荷主との契約内容による
    surcharge_total_yen = round(price_diff * SURCHARGE_PER_LITER * total_distance * 0.1, 0)

    s3_out = MicroAgentOutput(
        agent_name="surcharge_calc",
        success=True,
        result={
            "diesel_price_yen": diesel_price,
            "base_price_yen": BASE_DIESEL_PRICE,
            "price_diff": price_diff,
            "total_distance_km": total_distance,
            "surcharge_total_yen": surcharge_total_yen,
            "surcharge_applied": price_diff > 0,
        },
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "surcharge_calc", "surcharge_calc", s3_out)
    context["surcharge_result"] = s3_out.result

    # ─── Step 4: rule_matcher（標準運賃告示との照合）────────────────────
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "standard_freight_rate",
            "items": shipments,
            "base_freight_total": base_freight_total,
        },
        context=context,
    ))
    _add_step(4, "rule_matcher", "rule_matcher", s4_out)
    context["rate_compliance"] = s4_out.result

    # ─── Step 5: invoice_generator（請求書PDF生成）──────────────────────
    subtotal = base_freight_total + surcharge_total_yen
    tax_amount = round(subtotal * CONSUMPTION_TAX_RATE, 0)
    total_with_tax = subtotal + tax_amount

    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "請求書（運送）",
            "variables": {
                "billing_month": input_data.get("billing_month", ""),
                "shipper_name": input_data.get("shipper_name", ""),
                "shipment_details": shipment_details,
                "base_freight_total": base_freight_total,
                "surcharge_total_yen": surcharge_total_yen,
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_with_tax": total_with_tax,
                "bank_info": input_data.get("bank_info", {}),
            },
        },
        context=context,
    ))
    _add_step(5, "invoice_generator", "document_generator", s5_out)
    context["invoice_doc"] = s5_out.result

    # ─── Step 6: validator（請求金額整合性チェック）─────────────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "shipper_name": input_data.get("shipper_name", ""),
                "billing_month": input_data.get("billing_month", ""),
                "total_with_tax": total_with_tax,
                "bank_info": input_data.get("bank_info", {}),
                "invoice_doc": s5_out.result,
            },
            "required_fields": [
                "shipper_name", "billing_month", "total_with_tax", "bank_info"
            ],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", s6_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    logger.info(
        f"freight_billing_pipeline: company_id={company_id}, "
        f"shipper={input_data.get('shipper_name', '')}, "
        f"total_yen={total_with_tax}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 請求書送付通知実装
            "invoice_total_yen": total_with_tax,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "base_freight_total": base_freight_total,
        "surcharge_total_yen": surcharge_total_yen,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_with_tax": total_with_tax,
        "invoice_doc": s5_out.result,
        "rate_compliance": s4_out.result,
    }

    return FreightBillingResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

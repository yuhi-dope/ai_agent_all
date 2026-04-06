"""物流・運送業 傭車管理パイプライン

Steps:
  Step 1: extractor           傭車依頼データ構造化（案件×傭車先×依頼内容）
  Step 2: vendor_matcher      傭車先マスタ照合（対応エリア・車種・評価スコア）
  Step 3: request_generator   傭車依頼書生成（依頼書PDF）
  Step 4: compliance_checker  下請法チェック（書面交付義務・60日以内支払い）
  Step 5: cost_calculator     傭車コスト計算（傭車料+通行料+燃料サーチャージ）
  Step 6: validator           傭車先選定妥当性チェック（緑ナンバー確認・実績）
  Step 7: saas_writer         execution_logs保存 + 傭車台帳更新通知
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

# 下請法：支払い期限（日）
SUBCONTRACT_PAYMENT_LIMIT_DAYS = 60

# 下請法：書面交付義務の必須記載項目
SUBCONTRACT_REQUIRED_FIELDS = [
    "依頼日", "傭車先事業者名", "業務内容", "運賃", "支払条件",
    "支払期日", "納期（運行日）",
]

# 傭車先評価スコア閾値
MIN_VENDOR_SCORE = 3.0  # 5段階評価で3以上


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
class CharterManagementResult:
    """傭車管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 傭車管理パイプライン",
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


async def run_charter_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> CharterManagementResult:
    """
    傭車管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "charter_date": str,           # 運行日 YYYY-MM-DD
            "request_date": str,           # 依頼日 YYYY-MM-DD
            "origin": str,                 # 積地
            "destination": str,            # 荷降ろし地
            "cargo": str,                  # 荷物名
            "weight_kg": float,
            "vehicle_type_required": str,  # 必要車種
            "vendor_candidates": list[{
                "vendor_id": str,
                "vendor_name": str,
                "license_no": str,         # 緑ナンバー（事業用）
                "area_coverage": list[str],
                "vehicle_types": list[str],
                "score": float,            # 評価スコア 1-5
                "charter_rate_yen": float, # 傭車料
            }],
            "payment_terms": {
                "payment_date": str,       # 支払予定日 YYYY-MM-DD
                "method": str,             # 銀行振込等
            },
        }

    Returns:
        CharterManagementResult
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

    def _fail(step_name: str) -> CharterManagementResult:
        return CharterManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    vendors = input_data.get("vendor_candidates", [])

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "charter_date": "string",
                "origin": "string",
                "destination": "string",
                "cargo": "string",
                "weight_kg": "float",
                "vehicle_type_required": "string",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: vendor_matcher（傭車先マスタ照合）──────────────────────
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "vendor_matching",
            "items": vendors,
            "requirements": {
                "vehicle_type": input_data.get("vehicle_type_required", ""),
                "area": input_data.get("destination", ""),
                "min_score": MIN_VENDOR_SCORE,
            },
        },
        context=context,
    ))
    _add_step(2, "vendor_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("vendor_matcher")

    # スコア上位の傭車先を選定
    eligible_vendors = [v for v in vendors if v.get("score", 0) >= MIN_VENDOR_SCORE]
    selected_vendor = max(eligible_vendors, key=lambda v: v.get("score", 0)) if eligible_vendors else (vendors[0] if vendors else {})
    context["selected_vendor"] = selected_vendor

    # ─── Step 3: request_generator（傭車依頼書生成）────────────────────
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "傭車依頼書",
            "variables": {
                "request_date": input_data.get("request_date", ""),
                "charter_date": input_data.get("charter_date", ""),
                "vendor_name": selected_vendor.get("vendor_name", ""),
                "origin": input_data.get("origin", ""),
                "destination": input_data.get("destination", ""),
                "cargo": input_data.get("cargo", ""),
                "weight_kg": input_data.get("weight_kg", 0.0),
                "vehicle_type_required": input_data.get("vehicle_type_required", ""),
                "charter_rate_yen": selected_vendor.get("charter_rate_yen", 0),
                "payment_terms": input_data.get("payment_terms", {}),
            },
        },
        context=context,
    ))
    _add_step(3, "request_generator", "document_generator", s3_out)
    context["charter_request_doc"] = s3_out.result

    # ─── Step 4: compliance_checker（下請法チェック）────────────────────
    s4_start = int(time.time() * 1000)
    subcontract_violations: list[str] = []

    payment_terms = input_data.get("payment_terms", {})
    payment_date_str = payment_terms.get("payment_date", "")
    request_date_str = input_data.get("request_date", "")

    if payment_date_str and request_date_str:
        try:
            from datetime import date
            request_date = date.fromisoformat(request_date_str)
            payment_date = date.fromisoformat(payment_date_str)
            days_to_payment = (payment_date - request_date).days
            if days_to_payment > SUBCONTRACT_PAYMENT_LIMIT_DAYS:
                subcontract_violations.append(
                    f"支払期日が下請法60日超過: {days_to_payment}日後（{payment_date_str}）"
                )
        except ValueError:
            subcontract_violations.append("支払期日の日付形式が不正")

    if not selected_vendor.get("license_no"):
        subcontract_violations.append("傭車先の事業用ナンバー（緑ナンバー）未確認")

    s4_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "violations": subcontract_violations,
            "passed": len(subcontract_violations) == 0,
            "required_fields_checklist": SUBCONTRACT_REQUIRED_FIELDS,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance_checker", "compliance_checker", s4_out)
    context["subcontract_compliance"] = s4_out.result

    # ─── Step 5: cost_calculator（傭車コスト計算）───────────────────────
    s5_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "charter_cost",
            "charter_rate_yen": selected_vendor.get("charter_rate_yen", 0),
            "toll_yen": input_data.get("toll_yen", 0),
            "fuel_surcharge_yen": input_data.get("fuel_surcharge_yen", 0),
            "distance_km": input_data.get("distance_km", 0.0),
        },
        context=context,
    ))
    _add_step(5, "cost_calculator", "cost_calculator", s5_out)
    context["cost_result"] = s5_out.result

    # ─── Step 6: validator（傭車先選定妥当性チェック）──────────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "selected_vendor": selected_vendor,
                "charter_request_doc": s3_out.result,
                "subcontract_compliance": s4_out.result,
            },
            "required_fields": ["selected_vendor", "charter_request_doc"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", s6_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    logger.info(
        f"charter_management_pipeline: company_id={company_id}, "
        f"vendor={selected_vendor.get('vendor_name', '')}, "
        f"violations={len(subcontract_violations)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 下請法違反時はSlack通知
            "subcontract_violation_count": len(subcontract_violations),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "selected_vendor": selected_vendor,
        "charter_request_doc": s3_out.result,
        "subcontract_compliance": s4_out.result,
        "cost_result": s5_out.result,
        "subcontract_violations": subcontract_violations,
    }

    return CharterManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

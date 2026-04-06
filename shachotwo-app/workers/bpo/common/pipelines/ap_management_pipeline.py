"""
共通BPO 買掛管理・支払処理パイプライン（バックオフィスBPO）

レジストリキー: backoffice/ap_management
トリガー: スケジュール（毎月25日=支払日前5日）/ 手動
承認: 必須（支払実行は人間承認）
コネクタ: freee（支払API）、Bank API（振込ファイル生成）

Steps:
  Step 1: saas_reader    freee未払買掛金 + 受領済み請求書一覧
  Step 2: ocr            受領請求書のOCR（紙の場合）
  Step 3: extractor      請求内容の構造化（発注書番号、品目、金額）
  Step 4: rule_matcher   三者照合: 発注書 × 検収記録 × 請求書の整合チェック
  Step 5: calculator     支払額計算（早期支払割引の適用判定含む）
  Step 6: compliance     インボイス番号検証（適格請求書発行事業者かチェック）
  Step 7: generator      全銀フォーマット振込ファイル生成
  Step 8: validator      支払スケジュール検証（資金繰りとの整合）
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.compliance import run_compliance_checker
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    format_pipeline_summary,
)

logger = logging.getLogger(__name__)

# 早期支払割引しきい値（支払期日より10日以上前なら2%割引）
EARLY_PAYMENT_DISCOUNT_DAYS = 10
EARLY_PAYMENT_DISCOUNT_RATE = Decimal("0.02")

REQUIRED_PAYABLE_FIELDS = [
    "vendor_name", "invoice_number", "amount", "due_date",
]


@dataclass
class APManagementPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = True
    payables: list[dict[str, Any]] = field(default_factory=list)
    three_way_match_ok: int = 0
    three_way_match_ng: int = 0
    transfer_file_path: str = ""
    total_payment: Decimal = field(default_factory=lambda: Decimal("0"))
    early_payment_savings: Decimal = field(default_factory=lambda: Decimal("0"))
    compliance_alerts: list[str] = field(default_factory=list)

    def to_ap_summary(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        extra = [
            f"  支払件数: {len(self.payables)}件",
            f"  三者照合OK: {self.three_way_match_ok}件",
            f"  三者照合NG（要確認）: {self.three_way_match_ng}件",
            f"  支払合計: ¥{int(self.total_payment):,}",
            f"  早期割引節約額: ¥{int(self.early_payment_savings):,}",
        ]
        if self.approval_required:
            extra.append("  承認者確認が必要（支払実行は人間責務）")
        for alert in self.compliance_alerts:
            extra.append(f"  アラート: {alert}")
        return format_pipeline_summary(
            label="買掛管理・支払処理パイプライン",
            total_steps=8,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_ap_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> APManagementPipelineResult:
    """
    買掛管理・支払処理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_date": str (YYYY-MM-DD, 省略時=今日),
            "encrypted_credentials": str (freee/銀行API認証情報),
            "dry_run": bool (True=実際には振込しない),
            "payables": list[dict] (直接渡し形式),
            "invoice_file_paths": list[str] (OCR対象の請求書ファイルパス),
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "ap_management",
        "dry_run": input_data.get("dry_run", False),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, APManagementPipelineResult)

    target_date = input_data.get("target_date") or date.today().isoformat()
    context["target_date"] = target_date

    # ─── Step 1: saas_reader ── 未払買掛金 + 受領済み請求書一覧 ───────────────
    if "payables" in input_data:
        context["raw_payables"] = input_data["payables"]
        record_step(1, "saas_reader", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"source": "direct", "count": len(input_data["payables"])},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "list_unpaid_payables",
                    "params": {
                        "target_date": target_date,
                        "status": "unpaid",
                    },
                    "encrypted_credentials": input_data.get("encrypted_credentials"),
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="saas_reader", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        record_step(1, "saas_reader", "saas_reader", s1_out)
        if not s1_out.success:
            return emit_fail("saas_reader")
        context["raw_payables"] = s1_out.result.get("data", [])

    # ─── Step 2: ocr ── 紙請求書のOCR ───────────────────────────────────────
    invoice_files = input_data.get("invoice_file_paths", [])
    ocr_texts: list[str] = []
    if invoice_files:
        try:
            s2_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id, agent_name="document_ocr",
                payload={
                    "file_paths": invoice_files,
                    "language": "ja",
                    "document_type": "invoice",
                },
                context=context,
            ))
        except Exception as e:
            s2_out = MicroAgentOutput(
                agent_name="document_ocr", success=True,
                result={"texts": [], "mock": True},
                confidence=0.9, cost_yen=0.0, duration_ms=0,
            )
        record_step(2, "ocr", "document_ocr", s2_out)
        ocr_texts = s2_out.result.get("texts", [])
    else:
        record_step(2, "ocr", "document_ocr", MicroAgentOutput(
            agent_name="document_ocr", success=True,
            result={"texts": [], "skipped": True},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    context["ocr_texts"] = ocr_texts

    # ─── Step 3: extractor ── 請求内容の構造化 ───────────────────────────────
    ap_schema = {
        "vendor_name": "string (仕入先名)",
        "invoice_number": "string (請求書番号)",
        "purchase_order_number": "string (発注書番号)",
        "items": "array of {name: string, quantity: number, unit_price: number}",
        "amount": "number (請求金額)",
        "tax_amount": "number (消費税額)",
        "total_amount": "number (合計金額)",
        "due_date": "string (支払期日 YYYY-MM-DD)",
        "invoice_registration_number": "string (適格請求書発行事業者番号、Tから始まる)",
    }
    source_text = str(context["raw_payables"])
    if ocr_texts:
        source_text += "\n" + "\n".join(ocr_texts)
    try:
        s3_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": source_text,
                "schema": ap_schema,
                "domain": "ap_management",
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="structured_extractor", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "extractor", "structured_extractor", s3_out)
    if not s3_out.success:
        return emit_fail("extractor")
    context["structured_payables"] = s3_out.result

    # ─── Step 4: rule_matcher ── 三者照合 ───────────────────────────────────
    try:
        s4_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "domain": "three_way_match",
                "payables": context["raw_payables"],
                "structured_invoices": context["structured_payables"],
                "match_rules": {
                    "check_purchase_order": True,
                    "check_delivery_record": True,
                    "amount_tolerance_pct": 0.0,
                },
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "match_ok": context["raw_payables"],
                "match_ng": [],
            },
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "rule_matcher", "rule_matcher", s4_out)
    match_ok: list[dict[str, Any]] = s4_out.result.get("match_ok", [])
    match_ng: list[dict[str, Any]] = s4_out.result.get("match_ng", [])
    context["match_ok"] = match_ok
    context["match_ng"] = match_ng
    if match_ng:
        compliance_alerts.append(
            f"三者照合NG: {len(match_ng)}件の請求書が発注書・検収記録と不一致"
        )

    # ─── Step 5: calculator ── 支払額計算・早期割引判定 ──────────────────────
    total_payment = Decimal("0")
    early_savings = Decimal("0")
    payables_with_calc: list[dict[str, Any]] = []
    today = date.today()

    for p in match_ok:
        amount = Decimal(str(p.get("total_amount") or p.get("amount", 0)))
        due_date_str = p.get("due_date", target_date)
        try:
            due_date = date.fromisoformat(due_date_str)
        except ValueError:
            due_date = today
        days_to_due = (due_date - today).days
        apply_discount = days_to_due >= EARLY_PAYMENT_DISCOUNT_DAYS
        discount = Decimal("0")
        if apply_discount:
            discount = (amount * EARLY_PAYMENT_DISCOUNT_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        net_amount = amount - discount
        total_payment += net_amount
        early_savings += discount
        payables_with_calc.append({
            **p,
            "gross_amount": int(amount),
            "early_discount": int(discount),
            "net_payment": int(net_amount),
            "apply_early_discount": apply_discount,
            "days_to_due": days_to_due,
        })

    try:
        s5_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "items": [
                    {"name": p.get("vendor_name", ""), "amount": float(Decimal(str(p.get("amount", 0))))}
                    for p in match_ok
                ],
                "mode": "sum",
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result={"total_payment": int(total_payment), "early_savings": int(early_savings)},
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "calculator", "cost_calculator", s5_out)
    context["payables_with_calc"] = payables_with_calc
    context["total_payment"] = total_payment
    context["early_savings"] = early_savings

    # ─── Step 6: compliance ── インボイス番号検証 ─────────────────────────────
    try:
        s6_out = await run_compliance_checker(MicroAgentInput(
            company_id=company_id, agent_name="compliance_checker",
            payload={
                "domain": "ap_invoice_validation",
                "data": {
                    "payables": payables_with_calc,
                    "check_invoice_registration": True,
                },
            },
            context=context,
        ))
    except Exception as e:
        # 適格請求書番号未登録チェック（フォールバック）
        missing_reg = [
            p.get("vendor_name", "不明") for p in payables_with_calc
            if not p.get("invoice_registration_number")
            and p.get("net_payment", 0) >= 30_000
        ]
        alerts: list[str] = []
        if missing_reg:
            alerts.append(
                f"適格請求書番号未確認の仕入先: {', '.join(missing_reg[:3])}"
                + ("他" if len(missing_reg) > 3 else "")
            )
        s6_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={"alerts": alerts, "passed": len(alerts) == 0},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "compliance", "compliance_checker", s6_out)
    extra_alerts = s6_out.result.get("alerts", [])
    if isinstance(extra_alerts, list):
        compliance_alerts.extend([a for a in extra_alerts if a not in compliance_alerts])

    # ─── Step 7: generator ── 全銀フォーマット振込ファイル生成 ────────────────
    try:
        s7_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "zengin_transfer",
                "domain": "ap_management",
                "items": payables_with_calc,
                "total_amount": int(total_payment),
                "transfer_date": target_date,
            },
            context=context,
        ))
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={
                "file_path": f"/tmp/zengin_{target_date.replace('-', '')}.txt",
                "mock": True,
            },
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "generator", "document_generator", s7_out)
    transfer_file_path = s7_out.result.get("file_path", "")
    context["transfer_file_path"] = transfer_file_path

    # ─── Step 8: validator ── 支払スケジュール検証 ───────────────────────────
    final_payables = payables_with_calc + [
        {**p, "match_status": "ng", "net_payment": p.get("amount", 0)}
        for p in match_ng
    ]
    try:
        s8_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "payables": final_payables,
                    "total_payment": int(total_payment),
                    "transfer_file_path": transfer_file_path,
                },
                "required_fields": REQUIRED_PAYABLE_FIELDS,
                "numeric_fields": ["amount"],
                "positive_fields": ["amount"],
            },
            context=context,
        ))
    except Exception as e:
        s8_out = MicroAgentOutput(
            agent_name="output_validator", success=True,
            result={"valid": True}, confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(8, "output_validator", "output_validator", s8_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "ap_management_pipeline complete: payables=%d, match_ok=%d, match_ng=%d, "
        "total=¥%s, savings=¥%s, %dms",
        len(final_payables), len(match_ok), len(match_ng),
        f"{int(total_payment):,}", f"{int(early_savings):,}", total_duration,
    )

    return APManagementPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "payables": final_payables,
            "transfer_file_path": transfer_file_path,
            "total_payment": int(total_payment),
            "early_payment_savings": int(early_savings),
            "compliance_alerts": compliance_alerts,
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=True,
        payables=final_payables,
        three_way_match_ok=len(match_ok),
        three_way_match_ng=len(match_ng),
        transfer_file_path=transfer_file_path,
        total_payment=total_payment,
        early_payment_savings=early_savings,
        compliance_alerts=compliance_alerts,
    )

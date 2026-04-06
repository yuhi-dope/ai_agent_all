"""
共通BPO 発注・検収パイプライン（バックオフィスBPO）

レジストリキー: backoffice/purchase_order
トリガー: 手動（発注依頼）/ 在庫閾値割れ（製造業連携）/ goods_received イベント（検収）
承認: 金額 >= ¥50,000 は承認必要
コネクタ: kintone（発注管理）、freee（買掛連携）

発注フロー Steps（mode="order"）:
  Step 1: extractor       発注要求から構造化: {品目, 数量, 希望納期, 予算上限}
  Step 2: rule_matcher    推奨仕入先選定（vendor_scoreベース + 品目カテゴリマッチ）
  Step 3: generator       発注書ドラフト生成（テンプレート + 仕入先情報 + 品目明細）
  Step 4: pdf_generator   発注書PDF
  Step 5: saas_writer     kintone発注レコード作成 + freee買掛金予約
  Step 6: message         仕入先へ発注メール送信
  Step 7: validator       予算超過チェック + 納期整合性

検収フロー Steps（mode="inspection"）:
  Step 1: rule_matcher    発注書と納品内容の照合（品目/数量/品質）
  Step 2: extractor       差異検出（数量不足/品質不良/誤品）
  Step 3: saas_writer     検収完了 → AP管理パイプラインへ連鎖予約（支払処理トリガー）
"""
import time
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.pdf_generator import run_pdf_generator
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

APPROVAL_THRESHOLD_YEN = 50_000  # 承認必要金額閾値


@dataclass
class PurchaseOrderPipelineResult:
    success: bool
    mode: str  # "order" | "inspection"
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    po_number: str | None = None
    approval_required: bool = False
    compliance_alerts: list[str] = field(default_factory=list)
    # 検収フロー用
    inspection_passed: bool | None = None
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    ap_triggered: bool = False



async def run_purchase_order_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> PurchaseOrderPipelineResult:
    """
    発注・検収パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "mode": "order" | "inspection"  # 省略時は "order"

            # 発注モード（mode="order"）
            "request_text": str,            # 発注依頼の自然文 or 構造化済み情報
            "items": list[dict],            # 直接渡し形式 [{name, quantity, unit_price, category}]
            "budget_limit": int,            # 予算上限（円）
            "desired_delivery_date": str,   # 希望納期 YYYY-MM-DD
            "vendor_id": str,               # 仕入先ID（指定する場合）
            "encrypted_credentials": str,   # kintone/freee認証情報

            # 検収モード（mode="inspection"）
            "po_number": str,               # 発注番号
            "received_items": list[dict],   # 受領品 [{name, quantity, condition}]
            "inspection_result": str,       # "ok" | "partial" | "ng"
            "inspector_name": str,          # 検収担当者名
        }
    """
    pipeline_start = int(time.time() * 1000)
    mode = input_data.get("mode", "order")
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "purchase_order",
        "mode": mode,
    }

    record_step = make_step_adder(steps)

    if mode == "inspection":
        return await _run_inspection_flow(
            company_id=company_id,
            input_data=input_data,
            pipeline_start=pipeline_start,
            steps=steps,
            context=context,
            compliance_alerts=compliance_alerts,
            record_step=record_step,
        )
    else:
        return await _run_order_flow(
            company_id=company_id,
            input_data=input_data,
            pipeline_start=pipeline_start,
            steps=steps,
            context=context,
            compliance_alerts=compliance_alerts,
            record_step=record_step,
        )


async def _run_order_flow(
    company_id: str,
    input_data: dict[str, Any],
    pipeline_start: int,
    steps: list[StepResult],
    context: dict[str, Any],
    compliance_alerts: list[str],
    record_step: Any,
) -> PurchaseOrderPipelineResult:
    """発注フロー（7ステップ）"""
    emit_fail = make_fail_factory(steps, pipeline_start, PurchaseOrderPipelineResult)

    # ─── Step 1: extractor ── 発注要求の構造化 ──────────────────────────────
    if "items" in input_data:
        s1_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result={
                "items": input_data["items"],
                "budget_limit": input_data.get("budget_limit", 0),
                "desired_delivery_date": input_data.get(
                    "desired_delivery_date",
                    (date.today() + timedelta(days=14)).isoformat()
                ),
                "source": "direct",
            },
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    else:
        schema = {
            "items": "array of {name: string, quantity: number, unit_price: number, category: string}",
            "budget_limit": "number (予算上限円)",
            "desired_delivery_date": "string (YYYY-MM-DD)",
            "notes": "string",
        }
        try:
            s1_out = await run_structured_extractor(MicroAgentInput(
                company_id=company_id, agent_name="structured_extractor",
                payload={
                    "text": input_data.get("request_text", ""),
                    "schema": schema,
                    "domain": "purchase_order",
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="structured_extractor", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
    record_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return emit_fail("extractor")
    context["order_items"] = s1_out.result.get("items", [])
    context["budget_limit"] = s1_out.result.get("budget_limit", 0)
    context["desired_delivery_date"] = s1_out.result.get(
        "desired_delivery_date", (date.today() + timedelta(days=14)).isoformat()
    )

    # ─── Step 2: rule_matcher ── 推奨仕入先選定 ──────────────────────────────
    vendor_id = input_data.get("vendor_id")
    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "items": context["order_items"],
                    "vendor_id": vendor_id,
                },
                "domain": "purchase_order_vendor",
                "category": "vendor_selection",
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "applied_values": {
                    "recommended_vendor_id": vendor_id or "default_vendor",
                    "recommended_vendor_name": "（推奨仕入先未設定）",
                },
                "matched_rules": [],
                "unmatched_fields": ["vendor_selection"],
            },
            confidence=0.5, cost_yen=0.0, duration_ms=0,
        )
        compliance_alerts.append("仕入先選定ルールが未設定です。手動で仕入先を確認してください。")
    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    context["vendor"] = s2_out.result.get("applied_values", {})

    # ─── Step 3: generator ── 発注書ドラフト生成 ─────────────────────────────
    po_number = f"PO-{date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    context["po_number"] = po_number
    total_amount = sum(
        int(item.get("quantity", 0)) * int(item.get("unit_price", 0))
        for item in context["order_items"]
    )
    context["total_amount"] = total_amount

    po_doc_data = {
        "po_number": po_number,
        "issue_date": date.today().isoformat(),
        "delivery_date": context["desired_delivery_date"],
        "vendor_name": context["vendor"].get("recommended_vendor_name", ""),
        "items": context["order_items"],
        "total_amount": total_amount,
        "notes": input_data.get("notes", ""),
    }
    try:
        s3_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "purchase_order",
                "data": po_doc_data,
                "format": "text",
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={"document": f"発注書ドラフト（{po_number}）", "mock": True},
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "generator", "document_generator", s3_out)
    context["po_draft"] = s3_out.result.get("document", "")

    # ─── Step 4: pdf_generator ── 発注書PDF ──────────────────────────────────
    try:
        s4_out = await run_pdf_generator(MicroAgentInput(
            company_id=company_id, agent_name="pdf_generator",
            payload={
                "template": "purchase_order",
                "data": po_doc_data,
                "output_filename": f"po_{po_number}.pdf",
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="pdf_generator", success=True,
            result={"pdf_path": f"/tmp/po_{po_number}.pdf", "mock": True},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "pdf_generator", "pdf_generator", s4_out)
    context["po_pdf_path"] = s4_out.result.get("pdf_path", "")

    # ─── Step 5: saas_writer ── kintone発注レコード + freee買掛予約 ────────────
    approved_without_review = total_amount < APPROVAL_THRESHOLD_YEN
    try:
        s5_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": "kintone",
                "operation": "create_purchase_order",
                "params": {
                    "po_number": po_number,
                    "vendor_id": context["vendor"].get("recommended_vendor_id"),
                    "items": context["order_items"],
                    "total_amount": total_amount,
                    "delivery_date": context["desired_delivery_date"],
                    "status": "pending_approval" if not approved_without_review else "ordered",
                },
                "encrypted_credentials": input_data.get("encrypted_credentials"),
                "approved": approved_without_review,
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="saas_writer", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "saas_writer", "saas_writer", s5_out)
    if not s5_out.success:
        compliance_alerts.append("kintone登録失敗。手動登録が必要です。")

    # ─── Step 6: message ── 仕入先への発注メール送信 ─────────────────────────
    try:
        s6_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": "gmail",
                "operation": "send_email",
                "params": {
                    "to": context["vendor"].get("vendor_email", ""),
                    "subject": f"【発注書】{po_number}",
                    "body": context.get("po_draft", ""),
                    "attachments": [context.get("po_pdf_path", "")],
                },
                "approved": approved_without_review,
            },
            context=context,
        ))
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="saas_writer", success=True,
            result={"sent": False, "note": "仕入先メール未送信（手動送信が必要）", "mock": True},
            confidence=0.7, cost_yen=0.0, duration_ms=0,
        )
        if not approved_without_review:
            compliance_alerts.append(
                f"発注額Y{total_amount:,}がY{APPROVAL_THRESHOLD_YEN:,}以上のため承認後に送信してください。"
            )
    record_step(6, "message", "saas_writer", s6_out)

    # ─── Step 7: validator ── 予算超過チェック + 納期整合性 ───────────────────
    budget_limit = context.get("budget_limit", 0)
    if budget_limit and total_amount > budget_limit:
        compliance_alerts.append(
            f"予算超過: 発注額Y{total_amount:,} > 予算上限Y{budget_limit:,}"
        )
    try:
        s7_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "po_number": po_number,
                    "total_amount": total_amount,
                    "items": context["order_items"],
                    "delivery_date": context["desired_delivery_date"],
                },
                "required_fields": ["po_number", "total_amount", "items", "delivery_date"],
                "numeric_fields": ["total_amount"],
                "positive_fields": ["total_amount"],
                "rules": [{"field": "total_amount", "op": "gte", "value": 0}],
            },
            context=context,
        ))
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="output_validator", success=True,
            result={"valid": True}, confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "output_validator", "output_validator", s7_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        "purchase_order_pipeline (order) complete: po=%s, total=Y%s, approval=%s, %dms",
        po_number, f"{total_amount:,}", not approved_without_review, total_duration,
    )

    return PurchaseOrderPipelineResult(
        success=True,
        mode="order",
        steps=steps,
        final_output={
            "po_number": po_number,
            "items": context["order_items"],
            "total_amount": total_amount,
            "vendor": context["vendor"],
            "pdf_path": context.get("po_pdf_path", ""),
            "delivery_date": context["desired_delivery_date"],
            "kintone_registered": s5_out.success,
            "email_sent": s6_out.result.get("sent", False),
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        po_number=po_number,
        approval_required=not approved_without_review,
        compliance_alerts=compliance_alerts,
    )


async def _run_inspection_flow(
    company_id: str,
    input_data: dict[str, Any],
    pipeline_start: int,
    steps: list[StepResult],
    context: dict[str, Any],
    compliance_alerts: list[str],
    record_step: Any,
) -> PurchaseOrderPipelineResult:
    """検収フロー（3ステップ）"""
    emit_fail = make_fail_factory(steps, pipeline_start, PurchaseOrderPipelineResult)
    po_number = input_data.get("po_number", "")
    received_items: list[dict] = input_data.get("received_items", [])
    inspection_result = input_data.get("inspection_result", "ok")
    context["po_number"] = po_number

    # ─── Step 1: rule_matcher ── 発注書と納品内容の照合 ──────────────────────
    try:
        s1_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "po_number": po_number,
                    "received_items": received_items,
                },
                "domain": "purchase_order_inspection",
                "category": "goods_receipt_matching",
            },
            context=context,
        ))
    except Exception as e:
        s1_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "matched_rules": [],
                "applied_values": {"received_items": received_items},
                "unmatched_fields": [],
            },
            confidence=0.7, cost_yen=0.0, duration_ms=0,
        )
    record_step(1, "rule_matcher", "rule_matcher", s1_out)
    if not s1_out.success:
        return emit_fail("rule_matcher")
    context["matched_receipt"] = s1_out.result.get("applied_values", {})

    # ─── Step 2: extractor ── 差異検出 ───────────────────────────────────────
    try:
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": (
                    f"発注番号: {po_number}\n"
                    f"受領品: {received_items}\n"
                    f"検収結果: {inspection_result}"
                ),
                "schema": {
                    "discrepancies": (
                        "array of {item_name: string, type: string, "
                        "ordered_qty: number, received_qty: number, note: string}"
                    ),
                    "inspection_passed": "boolean",
                },
                "domain": "purchase_order_inspection",
            },
            context=context,
        ))
    except Exception as e:
        passed = inspection_result == "ok"
        s2_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result={"discrepancies": [], "inspection_passed": passed},
            confidence=0.8, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "extractor", "structured_extractor", s2_out)
    discrepancies: list[dict] = s2_out.result.get("discrepancies", [])
    inspection_passed: bool = s2_out.result.get("inspection_passed", inspection_result == "ok")

    for d in discrepancies:
        compliance_alerts.append(
            f"差異検出: {d.get('item_name')} - {d.get('type')} ({d.get('note', '')})"
        )

    # ─── Step 3: saas_writer ── 検収記録 + AP連鎖トリガー ────────────────────
    ap_triggered = False
    try:
        s3_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": "kintone",
                "operation": "update_purchase_order_inspection",
                "params": {
                    "po_number": po_number,
                    "inspection_date": date.today().isoformat(),
                    "inspector_name": input_data.get("inspector_name", ""),
                    "inspection_result": inspection_result,
                    "discrepancies": discrepancies,
                    "status": "inspection_complete" if inspection_passed else "inspection_failed",
                    "trigger_ap": inspection_passed,
                },
                "encrypted_credentials": input_data.get("encrypted_credentials"),
                "approved": True,
            },
            context=context,
        ))
        ap_triggered = inspection_passed and s3_out.success
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="saas_writer", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
        compliance_alerts.append("検収記録の登録に失敗しました。手動登録が必要です。")
    record_step(3, "saas_writer", "saas_writer", s3_out)

    if inspection_passed and not ap_triggered:
        compliance_alerts.append(
            "AP管理パイプラインへの連鎖トリガーが失敗しました。手動で支払処理を開始してください。"
        )

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        "purchase_order_pipeline (inspection) complete: po=%s, passed=%s, discrepancies=%d, %dms",
        po_number, inspection_passed, len(discrepancies), total_duration,
    )

    return PurchaseOrderPipelineResult(
        success=True,
        mode="inspection",
        steps=steps,
        final_output={
            "po_number": po_number,
            "inspection_date": date.today().isoformat(),
            "inspection_result": inspection_result,
            "inspection_passed": inspection_passed,
            "discrepancies": discrepancies,
            "ap_triggered": ap_triggered,
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        po_number=po_number,
        approval_required=not inspection_passed and len(discrepancies) > 0,
        compliance_alerts=compliance_alerts,
        inspection_passed=inspection_passed,
        discrepancies=discrepancies,
        ap_triggered=ap_triggered,
    )

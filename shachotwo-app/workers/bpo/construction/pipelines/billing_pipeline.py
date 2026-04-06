"""
建設業 請求パイプライン（マイクロエージェント版）

Steps:
  Step 1: document_ocr         書類テキスト抽出
  Step 2: progress_extractor   出来高情報抽出（工種・進捗率・金額）
  Step 3: invoice_calculator   請求金額計算（出来高×契約額・消費税）
  Step 4: compliance_checker   インボイス制度・建設業法チェック
  Step 5: output_validator     必須記載事項チェック
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.validator import run_output_validator
from workers.micro.anomaly_detector import run_anomaly_detector

logger = logging.getLogger(__name__)

REQUIRED_INVOICE_FIELDS = ["invoice_number", "invoice_date", "client_name", "total", "items"]
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
class BillingPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_pending: bool = False

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 請求パイプライン",
            f"  ステップ: {len(self.steps)}/5",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "✅" if s.success else "❌"
            warn = f" ⚠️{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_billing_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    contract_id: str | None = None,
    period_year: int | None = None,
    period_month: int | None = None,
) -> BillingPipelineResult:
    """
    建設業請求パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"text": str} または {"file_path": str} または {"progress_items": list}
        contract_id: 工事契約ID（ある場合はDBから契約情報を取得）
        period_year: 請求対象年
        period_month: 請求対象月
    """
    from datetime import date
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    now = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "contract_id": contract_id,
        "period_year": period_year or now.year,
        "period_month": period_month or now.month,
    }

    def _add_step(step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput) -> StepResult:
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

    def _fail(step_name: str) -> BillingPipelineResult:
        return BillingPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: document_ocr ───────────────────────────────────────────
    if "progress_items" in input_data:
        context["progress_items"] = input_data["progress_items"]
        steps.append(StepResult(
            step_no=1, step_name="document_ocr", agent_name="document_ocr",
            success=True, result={"source": "direct_items"}, confidence=1.0,
            cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id, agent_name="document_ocr",
                payload={k: v for k, v in input_data.items() if k in ("text", "file_path")},
                context=context,
            ))
        except Exception as e:
            ocr_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": str(e)}, confidence=0.0,
                cost_yen=0.0, duration_ms=0,
            )
        _add_step(1, "document_ocr", "document_ocr", ocr_out)
        if not ocr_out.success:
            return _fail("document_ocr")
        context["raw_text"] = ocr_out.result.get("text", "")

    # ─── Step 2: progress_extractor ─────────────────────────────────────
    s2_start = int(time.time() * 1000)
    if "progress_items" in context:
        s2_out = MicroAgentOutput(
            agent_name="progress_extractor", success=True,
            result={"progress_items": context["progress_items"]},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    else:
        schema = {
            "progress_items": "list[{item_name: str, contract_amount: number, progress_rate: float}]",
            "contract_amount": "number",
            "client_name": "string",
            "project_name": "string",
        }
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": context.get("raw_text", ""), "schema": schema},
            context=context,
        ))
    step2 = _add_step(2, "progress_extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _fail("progress_extractor")
    progress_items = s2_out.result.get("progress_items", [])
    context["progress_items"] = progress_items
    context["client_name"] = s2_out.result.get("client_name", "")
    context["project_name"] = s2_out.result.get("project_name", "")

    # ─── Step 3: invoice_calculator ─────────────────────────────────────
    s3_start = int(time.time() * 1000)
    try:
        # contract_idがある場合はDBから契約情報を取得
        contract_amount = s2_out.result.get("contract_amount", 0)
        if contract_id:
            try:
                from db.supabase import get_service_client
                db = get_service_client()
                contract_row = db.table("construction_contracts").select(
                    "contract_amount, client_name, project_name, tax_rate"
                ).eq("id", contract_id).single().execute()
                if contract_row.data:
                    contract_amount = contract_row.data.get("contract_amount", contract_amount)
                    context["client_name"] = contract_row.data.get("client_name", context["client_name"])
                    context["project_name"] = contract_row.data.get("project_name", context["project_name"])
                    context["tax_rate"] = contract_row.data.get("tax_rate", "0.10")
            except Exception:
                pass

        tax_rate = Decimal(str(context.get("tax_rate", "0.10")))
        cumulative = sum(
            int(Decimal(str(item.get("contract_amount", contract_amount))) *
                Decimal(str(item.get("progress_rate", 0))))
            for item in progress_items
        )
        # 前回累計はゼロとして当月請求額＝累計（簡易版）
        subtotal = cumulative
        tax_amount = int(subtotal * tax_rate)
        total = subtotal + tax_amount
        invoice_number = f"INV-{context['period_year']}{context['period_month']:02d}-001"

        s3_out = MicroAgentOutput(
            agent_name="invoice_calculator", success=True,
            result={
                "invoice_number": invoice_number,
                "invoice_date": f"{context['period_year']}-{context['period_month']:02d}-25",
                "client_name": context["client_name"],
                "project_name": context["project_name"],
                "subtotal": subtotal,
                "tax_rate": float(tax_rate),
                "tax_amount": tax_amount,
                "total": total,
                "items": progress_items,
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="invoice_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "invoice_calculator", "invoice_calculator", s3_out)
    if not s3_out.success:
        return _fail("invoice_calculator")
    context["invoice"] = s3_out.result

    # ─── Step 4: compliance_checker ─────────────────────────────────────
    s4_start = int(time.time() * 1000)
    warnings: list[str] = []
    invoice = context["invoice"]
    # インボイス制度: 適格請求書発行事業者登録番号チェック（簡易）
    if not context.get("invoice_registration_number"):
        warnings.append("適格請求書発行事業者登録番号が未設定（インボイス制度対応要確認）")
    # 請求金額チェック
    if invoice.get("total", 0) <= 0:
        warnings.append("請求金額が0以下です")
    # 消費税率チェック（10%以外は要確認）
    if abs(invoice.get("tax_rate", 0.10) - 0.10) > 0.001:
        warnings.append(f"消費税率が標準外: {invoice['tax_rate']:.1%}")

    s4_out = MicroAgentOutput(
        agent_name="compliance_checker", success=True,
        result={"warnings": warnings, "passed": len(warnings) == 0},
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance_checker", "compliance_checker", s4_out)
    context["compliance_warnings"] = warnings

    # ─── Step 5: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": invoice,
            "required_fields": REQUIRED_INVOICE_FIELDS,
            "numeric_fields": ["subtotal", "tax_amount", "total"],
            "positive_fields": ["total"],
        },
        context=context,
    ))
    _add_step(5, "output_validator", "output_validator", val_out)

    # ─── Step 6: anomaly_detector ────────────────────────────────────────
    # 請求金額・消費税の桁間違い・消費税率異常を検知する
    s6_start = int(time.time() * 1000)
    anomaly_items = [
        {"name": "subtotal", "value": invoice.get("subtotal", 0)},
        {"name": "tax_amount", "value": invoice.get("tax_amount", 0)},
        {"name": "total", "value": invoice.get("total", 0)},
    ]
    anomaly_rules = [
        {
            "field": "tax_amount",
            "operator": "lte",
            "threshold": invoice.get("subtotal", 0) * 0.101 if invoice.get("subtotal") else 0,
            "message": "消費税額が税抜金額の10%を超えています。消費税率を確認してください",
        },
        {
            "field": "tax_amount",
            "operator": "gte",
            "threshold": invoice.get("subtotal", 0) * 0.099 if invoice.get("subtotal") else 0,
            "message": "消費税額が税抜金額の10%を下回っています。消費税率を確認してください",
        },
    ]
    try:
        anomaly_out = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id,
            agent_name="anomaly_detector",
            payload={
                "items": anomaly_items,
                "rules": anomaly_rules,
                "detect_modes": ["digit_error", "rules"],
            },
            context=context,
        ))
    except Exception as e:
        anomaly_out = MicroAgentOutput(
            agent_name="anomaly_detector", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s6_start,
        )
    steps.append(StepResult(
        step_no=6, step_name="anomaly_detector", agent_name="anomaly_detector",
        success=anomaly_out.success,
        result=anomaly_out.result,
        confidence=anomaly_out.confidence,
        cost_yen=anomaly_out.cost_yen,
        duration_ms=anomaly_out.duration_ms,
    ))

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"billing_pipeline complete: total=¥{invoice.get('total', 0):,}, "
        f"cost=¥{total_cost_yen:.2f}, {total_duration}ms"
    )

    final_output = {**invoice, "compliance_warnings": warnings}
    if anomaly_out.success and anomaly_out.result.get("anomaly_count", 0) > 0:
        final_output["anomaly_warnings"] = anomaly_out.result["anomalies"]
    return BillingPipelineResult(
        success=True, steps=steps, final_output=final_output,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
    )

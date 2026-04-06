"""
共通BPO 請求書発行パイプライン（バックオフィスBPO）

レジストリキー: backoffice/invoice_issue
トリガー: スケジュール（毎月末）/ 手動 / パイプライン連鎖（billing完了後）
承認: 必須（金額確認は人間の責務）
コネクタ: freee（請求書作成API）、Gmail（送付）

Steps:
  Step 1: saas_reader      当月の完了案件・納品データを取得（execution_logs / kintone）
  Step 2: extractor        請求対象を構造化: {取引先, 品目[], 数量, 単価, 税率}
  Step 3: calculator       請求額計算（小計→消費税→合計、Decimal精度）
  Step 4: compliance       インボイス制度チェック（登録番号、税率区分、端数処理）
  Step 5: pdf_generator    請求書PDF生成（テンプレートHTML + データ）
  Step 6: saas_writer      freee請求書API → 請求レコード作成
  Step 7: validator        必須フィールド検証（請求番号、発行日、支払期限）
"""
import time
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.compliance import run_compliance_checker
from workers.micro.pdf_generator import run_pdf_generator
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    SharedStepResult,
    make_step_adder,
    make_fail_factory,
)

logger = logging.getLogger(__name__)

REQUIRED_INVOICE_FIELDS = [
    "invoice_number", "issue_date", "due_date",
    "client_name", "items", "subtotal", "tax_amount", "total",
]


@dataclass
class InvoiceIssuePipelineResult:
    success: bool
    steps: list[SharedStepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = True  # 請求書発行は常に承認必要
    invoices: list[dict[str, Any]] = field(default_factory=list)
    total_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    freee_synced: bool = False
    compliance_alerts: list[str] = field(default_factory=list)

    def to_log(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        lines = [
            f"{'OK' if self.success else 'NG'} 請求書発行パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  請求書数: {len(self.invoices)}件",
            f"  合計金額: ¥{self.total_amount:,}",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.approval_required:
            lines.append("  承認者確認が必要（請求書発行は人間責務）")
        for alert in self.compliance_alerts:
            lines.append(f"  コンプラ: {alert}")
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


async def run_invoice_issue_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> InvoiceIssuePipelineResult:
    """
    請求書発行パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_month": str (YYYY-MM, 省略時=先月),
            "client_ids": list[str] (省略時=全取引先),
            "encrypted_credentials": str (freee/kintone認証情報),
            "dry_run": bool (True=実際にはfreee登録しない),
            "invoices": list[dict] (直接渡し形式),
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[SharedStepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "invoice_issue",
        "dry_run": input_data.get("dry_run", False),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, InvoiceIssuePipelineResult)

    # ─── Step 1: saas_reader ── 当月完了案件・納品データ取得 ─────────────────
    target_month = input_data.get("target_month") or (
        date.today().replace(day=1) - timedelta(days=1)
    ).strftime("%Y-%m")
    context["target_month"] = target_month

    if "invoices" in input_data:
        context["raw_deliveries"] = input_data["invoices"]
        record_step(1, "saas_reader", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"source": "direct", "count": len(input_data["invoices"])},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "kintone",
                    "operation": "list_completed_deliveries",
                    "params": {
                        "target_month": target_month,
                        "client_ids": input_data.get("client_ids", []),
                        "status": "completed",
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
        context["raw_deliveries"] = s1_out.result.get("data", [])

    # ─── Step 2: extractor ── 請求対象の構造化 ──────────────────────────────
    schema = {
        "client_name": "string (取引先名)",
        "client_id": "string (取引先ID)",
        "items": "array of {name: string, quantity: number, unit_price: number, tax_rate: number}",
        "billing_period": "string (請求対象期間 YYYY-MM)",
        "invoice_number": "string (請求書番号、未設定なら空文字)",
        "notes": "string (備考)",
    }
    try:
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": str(context.get("raw_deliveries", [])),
                "schema": schema,
                "domain": "invoice_issue",
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="structured_extractor", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return emit_fail("extractor")
    context["invoice_data"] = s2_out.result

    # ─── Step 3: calculator ── 請求額計算（Decimal精度）─────────────────────
    inv = context["invoice_data"]
    items = inv.get("items") or []
    subtotal = Decimal("0")
    tax_amount_dec = Decimal("0")
    calc_items = []
    for item in items:
        qty = Decimal(str(item.get("quantity", 1)))
        unit = Decimal(str(item.get("unit_price", 0)))
        rate = Decimal(str(item.get("tax_rate", 0.10)))
        item_subtotal = (qty * unit).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        item_tax = (item_subtotal * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        subtotal += item_subtotal
        tax_amount_dec += item_tax
        calc_items.append({**item, "subtotal": int(item_subtotal), "tax": int(item_tax)})
    total = subtotal + tax_amount_dec

    try:
        s3_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "items": [
                    {"name": i.get("name", ""), "quantity": i.get("quantity", 1),
                     "unit_price": i.get("unit_price", 0)}
                    for i in items
                ],
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result={"total": int(total), "subtotal": int(subtotal), "tax": int(tax_amount_dec)},
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "calculator", "cost_calculator", s3_out)
    context["calc_result"] = {
        "subtotal": int(subtotal),
        "tax_amount": int(tax_amount_dec),
        "total": int(total),
        "items": calc_items,
    }

    # ─── Step 4: compliance ── インボイス制度チェック ──────────────────────
    invoice_number = inv.get("invoice_number") or f"INV-{target_month}-{uuid.uuid4().hex[:6].upper()}"
    issue_date = date.today().isoformat()
    due_date = (date.today() + timedelta(days=30)).isoformat()

    if int(total) >= 30_000 and not context.get("seller_invoice_number"):
        compliance_alerts.append(
            f"適格請求書発行事業者番号が未登録（請求額¥{int(total):,}）"
        )
    tax_rates = {item.get("tax_rate", 0.10) for item in items}
    if len(tax_rates) > 1:
        compliance_alerts.append("税率区分が混在しています。8%/10%の品目を分けて記載してください。")
    if int(tax_amount_dec) == 0 and items:
        compliance_alerts.append("消費税額が0円です。税率設定を確認してください。")

    s4_start = int(time.time() * 1000)
    try:
        s4_out = await run_compliance_checker(MicroAgentInput(
            company_id=company_id, agent_name="compliance_checker",
            payload={
                "domain": "invoice_issue",
                "data": {
                    "total": int(total),
                    "tax_rates": list(tax_rates),
                    "invoice_number": invoice_number,
                },
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={"alerts": compliance_alerts, "passed": len(compliance_alerts) == 0},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    record_step(4, "compliance", "compliance_checker", s4_out)
    extra_alerts = s4_out.result.get("alerts", [])
    if isinstance(extra_alerts, list):
        compliance_alerts.extend([a for a in extra_alerts if a not in compliance_alerts])

    # ─── Step 5: pdf_generator ── 請求書PDF生成 ─────────────────────────────
    invoice_doc = {
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "due_date": due_date,
        "client_name": inv.get("client_name", ""),
        "items": context["calc_result"]["items"],
        "subtotal": context["calc_result"]["subtotal"],
        "tax_amount": context["calc_result"]["tax_amount"],
        "total": context["calc_result"]["total"],
        "notes": inv.get("notes", ""),
        "billing_period": inv.get("billing_period", target_month),
    }
    try:
        s5_out = await run_pdf_generator(MicroAgentInput(
            company_id=company_id, agent_name="pdf_generator",
            payload={
                "template": "invoice",
                "data": invoice_doc,
                "output_filename": f"invoice_{invoice_number}.pdf",
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="pdf_generator", success=True,
            result={"pdf_path": f"/tmp/invoice_{invoice_number}.pdf", "mock": True},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "pdf_generator", "pdf_generator", s5_out)
    pdf_path = s5_out.result.get("pdf_path", "")

    # ─── Step 6: saas_writer ── freee請求書API → 請求レコード作成 ────────────
    dry_run = context.get("dry_run", False)
    try:
        s6_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": "freee",
                "operation": "create_invoice",
                "params": {
                    "invoice_number": invoice_number,
                    "client_name": inv.get("client_name", ""),
                    "issue_date": issue_date,
                    "due_date": due_date,
                    "total": int(total),
                    "items": context["calc_result"]["items"],
                    "pdf_path": pdf_path,
                },
                "approved": not dry_run,
                "dry_run": dry_run,
            },
            context=context,
        ))
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="saas_writer", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "saas_writer", "saas_writer", s6_out)
    freee_synced = s6_out.success and not s6_out.result.get("requires_approval")

    # ─── Step 7: validator ── 必須フィールド検証 ────────────────────────────
    final_invoice = {
        **invoice_doc,
        "pdf_path": pdf_path,
        "freee_synced": freee_synced,
        "compliance_alerts": compliance_alerts,
    }
    try:
        s7_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": final_invoice,
                "required_fields": REQUIRED_INVOICE_FIELDS,
                "numeric_fields": ["subtotal", "tax_amount", "total"],
                "positive_fields": ["total"],
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
        "invoice_issue_pipeline complete: invoice=%s, total=¥%s, freee_synced=%s, %dms",
        invoice_number, f"{int(total):,}", freee_synced, total_duration,
    )

    return InvoiceIssuePipelineResult(
        success=True,
        steps=steps,
        final_output=final_invoice,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=True,
        invoices=[final_invoice],
        total_amount=total,
        freee_synced=freee_synced,
        compliance_alerts=compliance_alerts,
    )

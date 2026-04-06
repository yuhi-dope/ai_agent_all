"""
共通BPO 経費処理パイプライン（マイクロエージェント版）

Steps:
  Step 1: document_ocr        レシート・領収書テキスト抽出
  Step 2: expense_extractor   経費情報抽出（日付・金額・科目・目的）
  Step 3: rule_matcher        経費規程照合（上限・申請区分・勘定科目）
  Step 4: cost_calculator     精算金額計算（上限超過カット・消費税分離）
  Step 5: compliance_checker  税務・インボイスコンプライアンスチェック
  Step 6: output_validator    申請書バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.validator import run_output_validator
from workers.micro.anomaly_detector import run_anomaly_detector

logger = logging.getLogger(__name__)

REQUIRED_EXPENSE_FIELDS = ["expense_date", "amount", "category", "purpose"]
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
class ExpensePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = False

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 経費処理パイプライン",
            f"  ステップ: {len(self.steps)}/6",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.approval_required:
            lines.append("  ⚠️ 承認者確認が必要")
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


async def run_expense_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    applicant_id: str | None = None,
) -> ExpensePipelineResult:
    """
    経費処理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"text": str} または {"file_path": str} または {"expense": dict}
            expense直渡し形式: {"expense_date": str, "amount": number, "category": str, "purpose": str}
        applicant_id: 申請者ユーザーID
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "applicant_id": applicant_id,
        "domain": "expense",
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

    def _fail(step_name: str) -> ExpensePipelineResult:
        return ExpensePipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: document_ocr ───────────────────────────────────────────
    if "expense" in input_data:
        context["expense_data"] = input_data["expense"]
        steps.append(StepResult(
            step_no=1, step_name="document_ocr", agent_name="document_ocr",
            success=True, result={"source": "direct_expense"}, confidence=1.0,
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
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        _add_step(1, "document_ocr", "document_ocr", ocr_out)
        if not ocr_out.success:
            return _fail("document_ocr")
        context["raw_text"] = ocr_out.result.get("text", "")

    # ─── Step 2: expense_extractor ───────────────────────────────────────
    s2_start = int(time.time() * 1000)
    if context.get("expense_data"):
        s2_out = MicroAgentOutput(
            agent_name="expense_extractor", success=True,
            result=context["expense_data"], confidence=1.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )
    else:
        schema = {
            "expense_date": "string (YYYY-MM-DD)",
            "amount": "number (税込金額)",
            "tax_amount": "number (消費税額)",
            "category": "string (交通費/交際費/消耗品費/会議費/通信費/その他)",
            "purpose": "string (使途・目的)",
            "vendor": "string (支払先)",
            "invoice_number": "string (適格請求書番号、あれば)",
        }
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": context.get("raw_text", ""), "schema": schema},
            context=context,
        ))

    _add_step(2, "expense_extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _fail("expense_extractor")
    context["expense"] = s2_out.result

    # ─── Step 3: rule_matcher ────────────────────────────────────────────
    category = context["expense"].get("category", "その他")
    rule_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id, agent_name="rule_matcher",
        payload={
            "input_values": {
                "category": category,
                "amount": context["expense"].get("amount", 0),
                "purpose": context["expense"].get("purpose", ""),
            },
            "domain": "expense_policy",
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", rule_out)
    matched_rules = rule_out.result.get("matched_rules", [])
    # 上限金額を規程から取得（デフォルト値）
    expense_limit = 50_000  # デフォルト上限
    for rule in matched_rules:
        lim = rule.get("limit_amount")
        if lim:
            expense_limit = int(lim)
            break
    context["expense_limit"] = expense_limit

    # ─── Step 4: cost_calculator ─────────────────────────────────────────
    amount = int(context["expense"].get("amount", 0))
    approved_amount = min(amount, expense_limit)
    calc_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id, agent_name="cost_calculator",
        payload={
            "items": [{"name": category, "quantity": 1, "unit_price": approved_amount}],
        },
        context=context,
    ))
    _add_step(4, "cost_calculator", "cost_calculator", calc_out)
    context["approved_amount"] = calc_out.result.get("total", approved_amount)
    context["over_limit"] = amount > expense_limit

    # ─── Step 5: compliance_checker ──────────────────────────────────────
    s5_start = int(time.time() * 1000)
    warnings: list[str] = []
    approval_required = False

    # 上限超過チェック
    if context["over_limit"]:
        warnings.append(
            f"上限超過: 申請額¥{amount:,} > 上限¥{expense_limit:,}"
            f"（承認額: ¥{context['approved_amount']:,}）"
        )
        approval_required = True

    # 交際費チェック（5,000円超は原則1人5,000円以内に制限）
    if category in ("交際費", "接待費") and amount > 5_000:
        warnings.append(f"交際費は1人5,000円超のため上長承認が必要（申請額: ¥{amount:,}）")
        approval_required = True

    # インボイス番号チェック（1万円以上は必要）
    if amount >= 10_000 and not context["expense"].get("invoice_number"):
        warnings.append("適格請求書番号が未記載（1万円以上は必要）")

    # 申請日チェック（60日以上前の経費は要確認）
    expense_date = context["expense"].get("expense_date", "")
    if expense_date:
        from datetime import date
        try:
            exp_d = date.fromisoformat(expense_date)
            days_elapsed = (date.today() - exp_d).days
            if days_elapsed > 60:
                warnings.append(f"経費発生から{days_elapsed}日経過（60日以内申請が原則）")
                approval_required = True
        except ValueError:
            pass

    s5_out = MicroAgentOutput(
        agent_name="compliance_checker", success=True,
        result={"warnings": warnings, "approval_required": approval_required, "passed": len(warnings) == 0},
        confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)

    # ─── Step 6: output_validator ────────────────────────────────────────
    expense_doc = {
        **context["expense"],
        "approved_amount": context["approved_amount"],
        "over_limit": context["over_limit"],
        "approval_required": approval_required,
        "compliance_warnings": warnings,
        "matched_rules": matched_rules,
    }
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": expense_doc,
            "required_fields": REQUIRED_EXPENSE_FIELDS,
            "numeric_fields": ["amount", "approved_amount"],
            "positive_fields": ["approved_amount"],
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    # ─── Step 7: anomaly_detector ────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    anomaly_items = [
        {"name": "amount", "value": context["expense"].get("amount", 0)},
        {"name": "approved_amount", "value": context["approved_amount"]},
    ]
    if context["expense"].get("tax_amount") is not None:
        anomaly_items.append({"name": "tax_amount", "value": context["expense"]["tax_amount"]})
    anomaly_rules = [
        {
            "field": "amount",
            "operator": "lte",
            "threshold": 10_000_000,
            "message": "1,000万円超の経費申請です。桁間違いがないか確認してください",
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
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s7_start,
        )
    steps.append(StepResult(
        step_no=7, step_name="anomaly_detector", agent_name="anomaly_detector",
        success=anomaly_out.success,
        result=anomaly_out.result,
        confidence=anomaly_out.confidence,
        cost_yen=anomaly_out.cost_yen,
        duration_ms=anomaly_out.duration_ms,
    ))
    if anomaly_out.success and anomaly_out.result.get("anomaly_count", 0) > 0:
        expense_doc["anomaly_warnings"] = anomaly_out.result["anomalies"]

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"expense_pipeline complete: amount=¥{amount:,}, approved=¥{context['approved_amount']:,}, "
        f"{total_duration}ms"
    )

    return ExpensePipelineResult(
        success=True, steps=steps, final_output=expense_doc,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
        approval_required=approval_required,
    )

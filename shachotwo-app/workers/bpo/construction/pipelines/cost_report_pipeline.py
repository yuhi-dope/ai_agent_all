"""
建設業 月次原価報告パイプライン（マイクロエージェント版）

Steps:
  Step 1: cost_reader          コスト実績データ読み込み
  Step 2: variance_calculator  予算vs実績 差異分析（工種別・累計・利益率）
  Step 3: risk_detector        収益リスク検出（赤字予測・コスト超過警告）
  Step 4: report_generator     月次原価報告書データ生成
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator

logger = logging.getLogger(__name__)

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
class CostReportPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    risk_alerts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 月次原価報告パイプライン",
            f"  ステップ: {len(self.steps)}/4",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        if self.risk_alerts:
            lines.append(f"  リスクアラート: {len(self.risk_alerts)}件")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" WARNING:{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_cost_report_pipeline(
    company_id: str,
    input_data: dict[str, Any],  # {"cost_records": list} or {"contract_id": str}
    period_year: int | None = None,
    period_month: int | None = None,
) -> CostReportPipelineResult:
    """
    建設業月次原価報告パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"cost_records": list} または {"contract_id": str}
                    cost_records の形式: [{"cost_type": str, "amount": int, "description": str}]
        period_year: 報告対象年
        period_month: 報告対象月
    """
    from datetime import date
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    now = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
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

    def _fail(step_name: str) -> CostReportPipelineResult:
        return CostReportPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: cost_reader ────────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    cost_records: list[dict[str, Any]] = []
    contract_amount: int = 0
    progress_rate: float | None = None

    if input_data.get("cost_records") is not None:
        cost_records = input_data["cost_records"]
        contract_amount = input_data.get("contract_amount", 0)
        progress_rate = input_data.get("progress_rate")
        s1_out = MicroAgentOutput(
            agent_name="cost_reader", success=True,
            result={
                "source": "direct_records",
                "record_count": len(cost_records),
                "contract_amount": contract_amount,
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    elif input_data.get("contract_id"):
        contract_id = input_data["contract_id"]
        try:
            from db.supabase import get_service_client
            db = get_service_client()
            # コスト実績取得
            cost_rows = db.table("construction_cost_records").select(
                "cost_type, amount, description"
            ).eq("contract_id", contract_id).eq("company_id", company_id).execute()
            cost_records = cost_rows.data or []
            # 契約金額取得
            contract_row = db.table("construction_contracts").select(
                "contract_amount, progress_rate"
            ).eq("id", contract_id).eq("company_id", company_id).single().execute()
            if contract_row.data:
                contract_amount = int(contract_row.data.get("contract_amount", 0))
                progress_rate = contract_row.data.get("progress_rate")
        except Exception as e:
            logger.warning(f"cost_reader DB取得エラー（非致命的）: {e}")

        # contract_amountが input_data に指定されていれば優先
        if input_data.get("contract_amount"):
            contract_amount = int(input_data["contract_amount"])

        s1_out = MicroAgentOutput(
            agent_name="cost_reader", success=True,
            result={
                "source": "db",
                "contract_id": contract_id,
                "record_count": len(cost_records),
                "contract_amount": contract_amount,
            },
            confidence=1.0 if cost_records else 0.5,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    else:
        # どちらもない場合は失敗
        s1_out = MicroAgentOutput(
            agent_name="cost_reader", success=False,
            result={"error": "cost_records または contract_id が必要です"},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    _add_step(1, "cost_reader", "cost_reader", s1_out)
    if not s1_out.success:
        return _fail("cost_reader")

    # cost_recordsが空の場合は失敗
    if not cost_records:
        steps[-1].success = False
        steps[-1].result["error"] = "コストレコードが空です"
        return _fail("cost_reader")

    context["cost_records"] = cost_records
    context["contract_amount"] = contract_amount
    context["progress_rate"] = progress_rate

    # ─── Step 2: variance_calculator ────────────────────────────────────
    s2_start = int(time.time() * 1000)
    try:
        # 工種別コスト集計
        cost_by_type: dict[str, int] = {}
        total_actual_cost = Decimal("0")

        for record in cost_records:
            cost_type = str(record.get("cost_type", "その他"))
            amount = Decimal(str(record.get("amount", 0)))
            cost_by_type[cost_type] = int(
                Decimal(str(cost_by_type.get(cost_type, 0))) + amount
            )
            total_actual_cost += amount

        total_actual_cost_int = int(total_actual_cost)
        contract_amount_dec = Decimal(str(contract_amount))
        profit = int(contract_amount_dec - total_actual_cost)
        profit_rate = (
            float((contract_amount_dec - total_actual_cost) / contract_amount_dec)
            if contract_amount_dec != 0
            else 0.0
        )

        s2_out = MicroAgentOutput(
            agent_name="variance_calculator", success=True,
            result={
                "cost_by_type": cost_by_type,
                "total_actual_cost": total_actual_cost_int,
                "contract_amount": contract_amount,
                "profit": profit,
                "profit_rate": profit_rate,
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        logger.error(f"variance_calculator error: {e}")
        s2_out = MicroAgentOutput(
            agent_name="variance_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "variance_calculator", "variance_calculator", s2_out)
    if not s2_out.success:
        return _fail("variance_calculator")

    cost_by_type = s2_out.result["cost_by_type"]
    total_actual_cost_int = s2_out.result["total_actual_cost"]
    profit = s2_out.result["profit"]
    profit_rate = s2_out.result["profit_rate"]
    context.update(s2_out.result)

    # ─── Step 3: risk_detector ──────────────────────────────────────────
    s3_start = int(time.time() * 1000)
    risk_alerts: list[str] = []

    if profit_rate < 0:
        risk_alerts.append(
            f"赤字: 利益率{profit_rate:.1%}（¥{profit:,}の損失）"
        )
    elif profit_rate < 0.05:
        risk_alerts.append(f"低収益警告: 利益率{profit_rate:.1%}")

    if progress_rate is not None:
        try:
            p = float(progress_rate)
            c = total_actual_cost_int / contract_amount if contract_amount > 0 else 0.0
            if c > p + 0.10:
                risk_alerts.append(
                    f"コスト超過: 進捗{p:.0%}に対しコスト消化{c:.0%}"
                )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    s3_out = MicroAgentOutput(
        agent_name="risk_detector", success=True,
        result={
            "risk_alerts": risk_alerts,
            "alert_count": len(risk_alerts),
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "risk_detector", "risk_detector", s3_out)
    context["risk_alerts"] = risk_alerts

    # ─── Step 4: report_generator ────────────────────────────────────────
    payload = {
        "template": "月次原価報告書",
        "variables": {
            "period": f"{context['period_year']}年{context['period_month']}月",
            "contract_amount": contract_amount,
            "total_actual_cost": total_actual_cost_int,
            "profit": profit,
            "profit_rate": profit_rate,
            "cost_by_type": cost_by_type,
            "risk_alerts": risk_alerts,
        },
    }
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template_name": "monthly_report",
            "data": payload,
            "format": "markdown",
        },
        context=context,
    ))
    _add_step(4, "report_generator", "document_generator", s4_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"cost_report_pipeline complete: profit_rate={profit_rate:.1%}, "
        f"alerts={len(risk_alerts)}, cost=¥{total_cost_yen:.2f}, {total_duration}ms"
    )

    final_output = {
        **s2_out.result,
        "risk_alerts": risk_alerts,
        "report_content": s4_out.result.get("content", ""),
        "period": f"{context['period_year']}年{context['period_month']}月",
    }
    return CostReportPipelineResult(
        success=True, steps=steps, final_output=final_output,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
        risk_alerts=risk_alerts,
    )

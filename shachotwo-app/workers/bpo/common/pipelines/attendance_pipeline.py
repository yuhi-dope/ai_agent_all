"""
共通BPO 勤怠管理パイプライン（マイクロエージェント版）

Steps:
  Step 1: attendance_reader    勤怠データ読み込み（直渡し/CSV/SaaS）
  Step 2: overtime_analyzer    残業時間集計・36協定チェック
  Step 3: absence_checker      欠勤・遅刻・有給残日数チェック
  Step 4: compliance_checker   労基法コンプライアンスチェック
  Step 5: output_validator     集計結果バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["period_year", "period_month", "employee_count", "total_overtime_hours"]
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 36協定の法定上限（月45時間。特別条項なしの場合）
OVERTIME_MONTHLY_LIMIT = 45


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
class AttendancePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 勤怠管理パイプライン",
            f"  ステップ: {len(self.steps)}/5",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.compliance_alerts:
            for alert in self.compliance_alerts:
                lines.append(f"  ⚠️ {alert}")
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


async def run_attendance_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    period_year: int | None = None,
    period_month: int | None = None,
) -> AttendancePipelineResult:
    """
    勤怠管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            {"employees": list}   — 直渡し（複数従業員）
            {"csv_text": str}     — CSVテキスト
        period_year: 勤怠対象年
        period_month: 勤怠対象月
    """
    from datetime import date
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    now = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "period_year": period_year or now.year,
        "period_month": period_month or (now.month - 1 if now.month > 1 else 12),
        "domain": "attendance",
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

    def _fail(step_name: str) -> AttendancePipelineResult:
        return AttendancePipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: attendance_reader ───────────────────────────────────────
    s1_start = int(time.time() * 1000)
    if "employees" in input_data:
        # 直渡し（複数従業員）
        employees: list[dict] = input_data["employees"]
        s1_out = MicroAgentOutput(
            agent_name="attendance_reader", success=True,
            result={"employees": employees, "source": "direct"},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
        )
    elif "csv_text" in input_data:
        # CSVからパース
        schema = {
            "employees": (
                "list[{employee_id: str, employee_name: str, work_days: int, "
                "work_hours: float, overtime_hours: float, absent_days: int, "
                "paid_leave_taken: int, paid_leave_remaining: int}]"
            ),
        }
        s1_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": input_data["csv_text"], "schema": schema},
            context=context,
        ))
        employees = s1_out.result.get("employees", [])
    else:
        # SaaS勤怠システムから取得（フォールバック）
        try:
            from workers.micro.saas_reader import run_saas_reader
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "attendance",
                    "operation": "get_monthly_attendance",
                    "params": {
                        "year": context["period_year"],
                        "month": context["period_month"],
                    },
                },
                context=context,
            ))
            employees = s1_out.result.get("employees", [])
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="attendance_reader", success=False,
                result={"error": str(e)}, confidence=0.0,
                cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
            )
            employees = []

    _add_step(1, "attendance_reader", "attendance_reader", s1_out)
    if not s1_out.success:
        return _fail("attendance_reader")
    context["employees"] = employees

    # ─── Step 2: overtime_analyzer ───────────────────────────────────────
    s2_start = int(time.time() * 1000)
    compliance_alerts: list[str] = []
    analyzed_employees: list[dict] = []
    try:
        for emp in employees:
            overtime_hours = float(emp.get("overtime_hours", 0.0))
            work_hours = float(emp.get("work_hours", 0.0))
            name = emp.get("employee_name", emp.get("employee_id", "不明"))

            # 36協定チェック
            if overtime_hours > OVERTIME_MONTHLY_LIMIT:
                compliance_alerts.append(
                    f"{name}: 月間残業{overtime_hours:.1f}時間（36協定上限{OVERTIME_MONTHLY_LIMIT}時間超過）"
                )

            # 月間総労働時間 = 通常労働時間 + 残業時間
            total_work_hours = work_hours + overtime_hours

            analyzed_employees.append({
                **emp,
                "total_work_hours": total_work_hours,
            })

        s2_out = MicroAgentOutput(
            agent_name="overtime_analyzer", success=True,
            result={"employees": analyzed_employees, "alerts": list(compliance_alerts)},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="overtime_analyzer", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "overtime_analyzer", "overtime_analyzer", s2_out)
    if not s2_out.success:
        return _fail("overtime_analyzer")
    context["analyzed_employees"] = s2_out.result["employees"]

    # ─── Step 3: absence_checker ─────────────────────────────────────────
    s3_start = int(time.time() * 1000)
    absence_alerts: list[str] = []
    try:
        for emp in context["analyzed_employees"]:
            name = emp.get("employee_name", emp.get("employee_id", "不明"))
            absent_days = int(emp.get("absent_days", 0))
            paid_leave_remaining = int(emp.get("paid_leave_remaining", 0))

            # 欠勤過多チェック
            if absent_days > 3:
                absence_alerts.append(f"{name}: 欠勤{absent_days}日（要確認）")

            # 有給残日数チェック（年5日取得義務）
            if paid_leave_remaining < 5:
                absence_alerts.append(
                    f"{name}: 有給残{paid_leave_remaining}日（年5日取得義務に注意）"
                )

        compliance_alerts.extend(absence_alerts)

        s3_out = MicroAgentOutput(
            agent_name="absence_checker", success=True,
            result={"alerts": absence_alerts, "employees": context["analyzed_employees"]},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="absence_checker", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "absence_checker", "absence_checker", s3_out)
    if not s3_out.success:
        return _fail("absence_checker")

    # ─── Step 4: compliance_checker ──────────────────────────────────────
    s4_start = int(time.time() * 1000)
    emp_list = context["analyzed_employees"]
    over_45h_alerts: list[str] = []
    try:
        overtime_values = [float(e.get("overtime_hours", 0.0)) for e in emp_list]
        avg_overtime = sum(overtime_values) / len(overtime_values) if overtime_values else 0.0
        over_45h_count = sum(1 for h in overtime_values if h > OVERTIME_MONTHLY_LIMIT)

        if over_45h_count > 0:
            over_45h_alerts.append(f"36協定超過: {over_45h_count}名（要是正）")

        if avg_overtime > 40:
            over_45h_alerts.append(f"平均残業{avg_overtime:.1f}h（過重労働リスク）")

        compliance_alerts.extend(over_45h_alerts)

        s4_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={
                "avg_overtime": avg_overtime,
                "over_45h_count": over_45h_count,
                "alerts": over_45h_alerts,
                "passed": len(over_45h_alerts) == 0,
            },
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="compliance_checker", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "compliance_checker", "compliance_checker", s4_out)
    if not s4_out.success:
        return _fail("compliance_checker")

    # ─── Step 5: output_validator ────────────────────────────────────────
    total_overtime_hours = sum(float(e.get("overtime_hours", 0.0)) for e in emp_list)
    total_work_hours = sum(float(e.get("total_work_hours", 0.0)) for e in emp_list)
    avg_overtime_hours = (
        total_overtime_hours / len(emp_list) if emp_list else 0.0
    )

    final_output: dict[str, Any] = {
        "period_year": context["period_year"],
        "period_month": context["period_month"],
        "employee_count": len(emp_list),
        "total_work_hours": total_work_hours,
        "total_overtime_hours": total_overtime_hours,
        "average_overtime_hours": avg_overtime_hours,
        "employees": emp_list,
        "compliance_alerts": compliance_alerts,
    }

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": final_output,
            "required_fields": REQUIRED_FIELDS,
            "numeric_fields": ["employee_count", "total_overtime_hours"],
            "positive_fields": ["employee_count"],
        },
        context=context,
    ))
    _add_step(5, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"attendance_pipeline complete: {len(emp_list)}名, "
        f"total_overtime={total_overtime_hours:.1f}h, {total_duration}ms"
    )

    return AttendancePipelineResult(
        success=True, steps=steps, final_output=final_output,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
        compliance_alerts=compliance_alerts,
    )

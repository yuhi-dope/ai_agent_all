"""
共通BPO 給与処理パイプライン（マイクロエージェント版）

Steps:
  Step 1: attendance_reader      勤怠データ読み込み（SaaS/CSV）
  Step 2: base_salary_calculator 基本給計算（固定給・時給・日給）
  Step 3: overtime_calculator    残業代計算（36協定アラート込み）
  Step 4: deduction_calculator   控除計算（社会保険・所得税・住民税）
  Step 5: compliance_checker     労基法コンプライアンスチェック
  Step 6: payslip_generator      給与明細データ生成
  Step 7: output_validator       明細バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

REQUIRED_PAYSLIP_FIELDS = ["employee_id", "period_year", "period_month", "gross_salary", "net_salary"]
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 36協定の法定上限（月45時間・年360時間。特別条項なしの場合）
OVERTIME_MONTHLY_LIMIT = 45
OVERTIME_ANNUAL_LIMIT = 360
# 割増賃金率
OVERTIME_RATE = Decimal("1.25")          # 法定時間外
LATE_NIGHT_RATE = Decimal("1.25")        # 深夜（22〜5時）
HOLIDAY_RATE = Decimal("1.35")           # 法定休日


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
class PayrollPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 給与処理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
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


async def run_payroll_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    period_year: int | None = None,
    period_month: int | None = None,
) -> PayrollPipelineResult:
    """
    給与処理パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            {"employee_id": str, "attendance": dict}  — 直渡し
            {"employees": list[{employee_id, ...}]}   — 複数従業員一括
            {"csv_text": str}                         — CSVテキスト
        period_year: 給与対象年
        period_month: 給与対象月
    """
    from datetime import date
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    now = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "period_year": period_year or now.year,
        "period_month": period_month or (now.month - 1 if now.month > 1 else 12),
        "domain": "payroll",
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

    def _fail(step_name: str) -> PayrollPipelineResult:
        return PayrollPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: attendance_reader ───────────────────────────────────────
    s1_start = int(time.time() * 1000)
    if "attendance" in input_data:
        # 直渡し（単一従業員）
        employees_attendance = [input_data["attendance"]]
        s1_out = MicroAgentOutput(
            agent_name="attendance_reader", success=True,
            result={"employees": employees_attendance, "source": "direct"},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
        )
    elif "employees" in input_data:
        employees_attendance = input_data["employees"]
        s1_out = MicroAgentOutput(
            agent_name="attendance_reader", success=True,
            result={"employees": employees_attendance, "source": "direct_batch"},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
        )
    elif "csv_text" in input_data:
        # CSVからパース
        schema = {
            "employees": "list[{employee_id: str, employee_name: str, work_days: int, "
                         "work_hours: float, overtime_hours: float, late_night_hours: float, "
                         "holiday_work_hours: float, absent_days: int, paid_leave_days: int}]",
        }
        s1_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": input_data["csv_text"], "schema": schema},
            context=context,
        ))
        employees_attendance = s1_out.result.get("employees", [])
    else:
        # SaaS勤怠システムから取得（フォールバック: SmartHR/freee勤怠）
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
            employees_attendance = s1_out.result.get("employees", [])
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="attendance_reader", success=False,
                result={"error": str(e)}, confidence=0.0,
                cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
            )
            employees_attendance = []

    _add_step(1, "attendance_reader", "attendance_reader", s1_out)
    if not s1_out.success:
        return _fail("attendance_reader")
    context["employees_attendance"] = employees_attendance

    # ─── Step 2: base_salary_calculator ──────────────────────────────────
    s2_start = int(time.time() * 1000)
    base_salaries: list[dict] = []
    try:
        for emp in employees_attendance:
            # 固定月給制（employee.monthly_salary）または時給制
            monthly_salary = emp.get("monthly_salary", 0)
            hourly_rate = emp.get("hourly_rate", 0)
            work_days = emp.get("work_days", 0)
            work_hours = emp.get("work_hours", 0.0)
            absent_days = emp.get("absent_days", 0)
            paid_leave_days = emp.get("paid_leave_days", 0)

            if monthly_salary:
                # 日割り控除（欠勤控除）
                # 当月所定労働日数（簡易: 20日固定）
                scheduled_days = emp.get("scheduled_days", 20)
                deduction_per_day = int(Decimal(str(monthly_salary)) / Decimal(str(scheduled_days)))
                base = monthly_salary - (deduction_per_day * absent_days)
            elif hourly_rate:
                base = int(Decimal(str(hourly_rate)) * Decimal(str(work_hours)))
            else:
                base = 0

            base_salaries.append({
                "employee_id": emp.get("employee_id", ""),
                "employee_name": emp.get("employee_name", ""),
                "base_salary": base,
                "work_days": work_days,
                "work_hours": work_hours,
                "absent_days": absent_days,
                "paid_leave_days": paid_leave_days,
                "overtime_hours": float(emp.get("overtime_hours", 0)),
                "late_night_hours": float(emp.get("late_night_hours", 0)),
                "holiday_work_hours": float(emp.get("holiday_work_hours", 0)),
                "hourly_rate": hourly_rate,
            })

        s2_out = MicroAgentOutput(
            agent_name="base_salary_calculator", success=True,
            result={"employees": base_salaries},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="base_salary_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "base_salary_calculator", "base_salary_calculator", s2_out)
    if not s2_out.success:
        return _fail("base_salary_calculator")
    context["base_salaries"] = s2_out.result["employees"]

    # ─── Step 3: overtime_calculator ─────────────────────────────────────
    s3_start = int(time.time() * 1000)
    compliance_alerts: list[str] = []
    overtime_results: list[dict] = []
    try:
        for emp in context["base_salaries"]:
            ot_hours = Decimal(str(emp.get("overtime_hours", 0)))
            ln_hours = Decimal(str(emp.get("late_night_hours", 0)))
            hol_hours = Decimal(str(emp.get("holiday_work_hours", 0)))
            # 時給単価（基本給 ÷ 月間所定労働時間 160h）
            base = emp.get("base_salary", 0)
            hourly = emp.get("hourly_rate") or int(Decimal(str(base)) / Decimal("160"))

            ot_pay = int(Decimal(str(hourly)) * ot_hours * OVERTIME_RATE)
            ln_pay = int(Decimal(str(hourly)) * ln_hours * LATE_NIGHT_RATE)
            hol_pay = int(Decimal(str(hourly)) * hol_hours * HOLIDAY_RATE)

            # 36協定チェック
            if float(ot_hours) > OVERTIME_MONTHLY_LIMIT:
                compliance_alerts.append(
                    f"{emp.get('employee_name', emp.get('employee_id'))}: "
                    f"月間残業{float(ot_hours):.1f}時間（36協定上限{OVERTIME_MONTHLY_LIMIT}時間超過）"
                )

            overtime_results.append({
                **emp,
                "overtime_pay": ot_pay,
                "late_night_pay": ln_pay,
                "holiday_pay": hol_pay,
                "total_overtime_pay": ot_pay + ln_pay + hol_pay,
                "gross_salary": emp["base_salary"] + ot_pay + ln_pay + hol_pay,
            })

        s3_out = MicroAgentOutput(
            agent_name="overtime_calculator", success=True,
            result={"employees": overtime_results, "alerts": compliance_alerts},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="overtime_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "overtime_calculator", "overtime_calculator", s3_out)
    if not s3_out.success:
        return _fail("overtime_calculator")
    context["overtime_results"] = s3_out.result["employees"]
    compliance_alerts.extend(s3_out.result.get("alerts", []))

    # ─── Step 4: deduction_calculator ────────────────────────────────────
    s4_start = int(time.time() * 1000)
    deduction_results: list[dict] = []
    try:
        for emp in context["overtime_results"]:
            gross = emp.get("gross_salary", 0)
            # 社会保険料（簡易: 標準報酬月額の約14.8%）
            health_insurance = int(Decimal(str(gross)) * Decimal("0.0499"))  # 健康保険
            welfare_pension = int(Decimal(str(gross)) * Decimal("0.0915"))  # 厚生年金
            employment_insurance = int(Decimal(str(gross)) * Decimal("0.006"))  # 雇用保険
            total_social = health_insurance + welfare_pension + employment_insurance
            # 所得税（源泉徴収。甲欄簡易計算）
            taxable = gross - total_social
            income_tax = _estimate_income_tax(taxable)
            # 住民税（前年度確定のため直接取得。デフォルト0）
            resident_tax = emp.get("resident_tax", 0)
            total_deductions = total_social + income_tax + resident_tax
            net_salary = gross - total_deductions

            deduction_results.append({
                **emp,
                "health_insurance": health_insurance,
                "welfare_pension": welfare_pension,
                "employment_insurance": employment_insurance,
                "social_insurance_total": total_social,
                "income_tax": income_tax,
                "resident_tax": resident_tax,
                "total_deductions": total_deductions,
                "net_salary": max(0, net_salary),
            })

        s4_out = MicroAgentOutput(
            agent_name="deduction_calculator", success=True,
            result={"employees": deduction_results},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="deduction_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "deduction_calculator", "deduction_calculator", s4_out)
    if not s4_out.success:
        return _fail("deduction_calculator")
    context["deduction_results"] = s4_out.result["employees"]

    # ─── Step 5: compliance_checker ──────────────────────────────────────
    s5_start = int(time.time() * 1000)
    for emp in context["deduction_results"]:
        net = emp.get("net_salary", 0)
        gross = emp.get("gross_salary", 0)
        # 最低賃金チェック（東京都: 1,163円/h として簡易）
        work_hours = emp.get("work_hours", 0)
        if work_hours > 0 and gross > 0:
            effective_hourly = int(gross / work_hours)
            min_wage = 1163  # TODO: 都道府県別に取得
            if effective_hourly < min_wage:
                compliance_alerts.append(
                    f"{emp.get('employee_name', emp.get('employee_id'))}: "
                    f"実質時給¥{effective_hourly} < 最低賃金¥{min_wage}"
                )
        # マイナス給与チェック
        if net < 0:
            compliance_alerts.append(
                f"{emp.get('employee_name', emp.get('employee_id'))}: "
                f"手取り額がマイナス（¥{net:,}）"
            )

    s5_out = MicroAgentOutput(
        agent_name="compliance_checker", success=True,
        result={"alerts": compliance_alerts, "passed": len(compliance_alerts) == 0},
        confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)

    # ─── Step 6: payslip_generator ───────────────────────────────────────
    payslips = []
    for emp in context["deduction_results"]:
        payslips.append({
            "employee_id": emp.get("employee_id"),
            "employee_name": emp.get("employee_name"),
            "period_year": context["period_year"],
            "period_month": context["period_month"],
            "base_salary": emp.get("base_salary", 0),
            "overtime_pay": emp.get("overtime_pay", 0),
            "late_night_pay": emp.get("late_night_pay", 0),
            "holiday_pay": emp.get("holiday_pay", 0),
            "gross_salary": emp.get("gross_salary", 0),
            "health_insurance": emp.get("health_insurance", 0),
            "welfare_pension": emp.get("welfare_pension", 0),
            "employment_insurance": emp.get("employment_insurance", 0),
            "income_tax": emp.get("income_tax", 0),
            "resident_tax": emp.get("resident_tax", 0),
            "total_deductions": emp.get("total_deductions", 0),
            "net_salary": emp.get("net_salary", 0),
        })

    s6_start = int(time.time() * 1000)
    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "給与明細",
            "variables": {
                "period": f"{context['period_year']}年{context['period_month']}月",
                "payslips": payslips,
                "employee_count": len(payslips),
            },
        },
        context=context,
    ))
    _add_step(6, "payslip_generator", "document_generator", gen_out)
    context["payslips"] = payslips

    # ─── Step 7: output_validator ────────────────────────────────────────
    final_doc = {
        "period_year": context["period_year"],
        "period_month": context["period_month"],
        "employee_count": len(payslips),
        "payslips": payslips,
        "compliance_alerts": compliance_alerts,
        "total_gross": sum(p["gross_salary"] for p in payslips),
        "total_net": sum(p["net_salary"] for p in payslips),
    }
    if payslips:
        sample = payslips[0]
        val_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": sample,
                "required_fields": REQUIRED_PAYSLIP_FIELDS,
                "numeric_fields": ["gross_salary", "net_salary"],
                "positive_fields": ["gross_salary"],
            },
            context=context,
        ))
    else:
        val_out = MicroAgentOutput(
            agent_name="output_validator", success=False,
            result={"error": "給与明細データが空です"},
            confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    _add_step(7, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"payroll_pipeline complete: {len(payslips)}名, "
        f"total_gross=¥{final_doc['total_gross']:,}, {total_duration}ms"
    )

    return PayrollPipelineResult(
        success=True, steps=steps, final_output=final_doc,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
        compliance_alerts=compliance_alerts,
    )


def _estimate_income_tax(taxable_monthly: int) -> int:
    """
    源泉徴収税額の簡易推計（扶養0人・甲欄）。
    実際は国税庁の月額表を使用すること。
    """
    annual = taxable_monthly * 12
    if annual <= 1_950_000:
        rate, deduction = Decimal("0.05"), 0
    elif annual <= 3_300_000:
        rate, deduction = Decimal("0.10"), 97_500
    elif annual <= 6_950_000:
        rate, deduction = Decimal("0.20"), 427_500
    elif annual <= 9_000_000:
        rate, deduction = Decimal("0.23"), 636_000
    elif annual <= 18_000_000:
        rate, deduction = Decimal("0.33"), 1_536_000
    else:
        rate, deduction = Decimal("0.40"), 2_796_000

    annual_tax = int(Decimal(str(annual)) * rate) - deduction
    return max(0, int(annual_tax / 12))

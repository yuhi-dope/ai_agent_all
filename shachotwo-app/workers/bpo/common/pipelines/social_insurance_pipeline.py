"""
共通BPO 社会保険届出パイプライン（マイクロエージェント版）

Steps:
  Step 1: filing_type_extractor   届出種別判定（資格取得/喪失/算定基礎/月額変更/賞与/産休育休）
  Step 2: employee_data_reader    従業員マスタ + 給与データ取得（SmartHR or freee）
  Step 3: standard_pay_calculator 標準報酬月額算出（4-6月平均 or 変更時3ヶ月平均）
  Step 4: premium_calculator      保険料計算（健保+厚年+雇保、事業主/本人負担按分）
  Step 5: filing_form_generator   届出書ドラフト生成（様式準拠JSON→PDF）
  Step 6: deadline_compliance     届出期限チェック（取得=5日以内、喪失=5日以内、算定=7/10）
  Step 7: output_validator        必須フィールド・計算整合性検証

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション2.3
"""
import time
import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.saas_reader import run_saas_reader
from workers.micro.generator import run_document_generator
from workers.micro.compliance import run_compliance_checker
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    steps_cost,
    steps_duration,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# 届出種別ラベル
FILING_TYPES = {
    "acquisition": "資格取得届",
    "loss": "資格喪失届",
    "standard_base": "算定基礎届",
    "monthly_change": "月額変更届",
    "bonus": "賞与支払届",
    "maternity": "産前産後休業取得者申出書",
    "childcare": "育児休業取得者申出書",
}

# 届出期限（日数）: 0 は個別計算
_FILING_DEADLINE_DAYS = {
    "acquisition": 5,
    "loss": 5,
    "bonus": 5,
}

# 標準報酬月額等級表（健康保険簡易版）: (標準報酬月額, 報酬月額上限) のリスト
_STANDARD_PAY_TABLE = [
    (58_000, 63_000), (68_000, 73_000), (78_000, 83_000), (88_000, 93_000),
    (98_000, 101_000), (104_000, 107_000), (110_000, 114_000), (118_000, 122_000),
    (126_000, 130_000), (134_000, 138_000), (142_000, 146_000), (150_000, 155_000),
    (160_000, 165_000), (170_000, 175_000), (180_000, 185_000), (190_000, 195_000),
    (200_000, 210_000), (220_000, 230_000), (240_000, 250_000), (260_000, 270_000),
    (280_000, 290_000), (300_000, 310_000), (320_000, 330_000), (340_000, 350_000),
    (360_000, 370_000), (380_000, 395_000), (410_000, 425_000), (440_000, 455_000),
    (470_000, 485_000), (500_000, 515_000), (530_000, 545_000), (560_000, 575_000),
    (590_000, 605_000), (620_000, 635_000), (650_000, 9_999_999),
]

# 保険料率（協会けんぽ東京都、2024年度）
_HEALTH_RATE = Decimal("0.09820")           # 健康保険 労使合計
_PENSION_RATE = Decimal("0.18300")           # 厚生年金 労使合計
_EMP_INS_TOTAL_RATE = Decimal("0.01550")     # 雇用保険 労使合計
_EMP_INS_EMPLOYEE_RATE = Decimal("0.006")    # 雇用保険 本人負担分
_BONUS_SMP_CAP = 1_500_000                   # 標準賞与額上限

REQUIRED_FILING_FIELDS = ["employee_id", "employee_name", "filing_type", "standard_monthly_pay"]


def _get_standard_monthly_pay(average_salary: int) -> int:
    """月額報酬から標準報酬月額（等級表の標準報酬）を返す。"""
    for standard, upper in _STANDARD_PAY_TABLE:
        if average_salary <= upper:
            return standard
    return _STANDARD_PAY_TABLE[-1][0]


def _resolve_filing_deadline(filing_type: str, event_date: date) -> date:
    """届出種別と事由発生日から届出期限日を算出する。"""
    if filing_type in _FILING_DEADLINE_DAYS:
        return event_date + timedelta(days=_FILING_DEADLINE_DAYS[filing_type])
    if filing_type == "standard_base":
        return date(event_date.year, 7, 10)
    if filing_type == "monthly_change":
        # 変動月の翌々月末日
        m = event_date.month + 2
        y = event_date.year
        if m > 12:
            m -= 12
            y += 1
        last_day = calendar.monthrange(y, m)[1]
        return date(y, m, last_day)
    return event_date + timedelta(days=30)


@dataclass
class SocialInsurancePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        extra = [f"  ALERT: {a}" for a in self.compliance_alerts]
        return pipeline_summary(
            label="社会保険届出パイプライン",
            total_steps=7,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra or None,
        )


async def run_social_insurance_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    filing_type: str | None = None,
    event_date: date | None = None,
) -> SocialInsurancePipelineResult:
    """
    社会保険届出パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            {"filing_type": str, "employee_ids": list[str], "event_date": str}
            {"filing_type": "standard_base", "target_year": int}
            {"employee_text": str}  -- 自由テキストから自動判定
            {"employees": list[dict]}  -- 従業員データ直渡し
        filing_type: 届出種別（input_data["filing_type"] がある場合は省略可）
        event_date: 事由発生日（入社日/退職日/賞与支払日等）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    compliance_alerts: list[str] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "social_insurance",
        "today": today.isoformat(),
    }

    def emit_fail(step_name: str) -> SocialInsurancePipelineResult:
        return SocialInsurancePipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=steps_cost(steps),
            total_duration_ms=steps_duration(steps, pipeline_start),
            failed_step=step_name,
            compliance_alerts=compliance_alerts,
        )

    # ─── Step 1: filing_type_extractor ───────────────────────────────────
    t1 = int(time.time() * 1000)
    resolved_type = filing_type or input_data.get("filing_type")
    resolved_date = event_date
    if not resolved_date and input_data.get("event_date"):
        try:
            resolved_date = date.fromisoformat(str(input_data["event_date"]))
        except (ValueError, TypeError):
            resolved_date = today
    if not resolved_date:
        resolved_date = today

    if resolved_type:
        s1_out = MicroAgentOutput(
            agent_name="filing_type_extractor", success=True,
            result={
                "filing_type": resolved_type,
                "filing_type_label": FILING_TYPES.get(resolved_type, resolved_type),
                "event_date": resolved_date.isoformat(),
                "employee_ids": input_data.get("employee_ids", []),
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t1,
        )
    elif "employee_text" in input_data:
        schema = {
            "filing_type": "str: one of acquisition|loss|standard_base|monthly_change|bonus|maternity|childcare",
            "event_date": "str: ISO date YYYY-MM-DD",
            "employee_ids": "list[str]",
            "reason": "str",
        }
        s1_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": input_data["employee_text"], "schema": schema},
            context=context,
        ))
        if s1_out.success:
            resolved_type = s1_out.result.get("filing_type", "acquisition")
            try:
                resolved_date = date.fromisoformat(
                    s1_out.result.get("event_date", today.isoformat())
                )
            except (ValueError, TypeError):
                resolved_date = today
    else:
        s1_out = MicroAgentOutput(
            agent_name="filing_type_extractor", success=False,
            result={"error": "filing_type または employee_text が必要です"},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t1,
        )

    record_step(1, "filing_type_extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return emit_fail("filing_type_extractor")

    context["filing_type"] = resolved_type
    context["event_date"] = resolved_date.isoformat()
    context["employee_ids"] = s1_out.result.get("employee_ids", input_data.get("employee_ids", []))

    # ─── Step 2: employee_data_reader ────────────────────────────────────
    t2 = int(time.time() * 1000)
    employees_raw: list[dict] = []

    if "employees" in input_data:
        employees_raw = input_data["employees"]
        s2_out = MicroAgentOutput(
            agent_name="employee_data_reader", success=True,
            result={"employees": employees_raw, "source": "direct"},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t2,
        )
    else:
        try:
            s2_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "smarthr",
                    "operation": "get_employees",
                    "params": {
                        "employee_ids": context["employee_ids"],
                        "include_salary": True,
                        "include_social_insurance": True,
                    },
                },
                context=context,
            ))
            employees_raw = s2_out.result.get("employees", [])
        except Exception as e:
            logger.warning(f"SmartHR取得失敗、freeeにフォールバック: {e}")
            try:
                s2_out = await run_saas_reader(MicroAgentInput(
                    company_id=company_id, agent_name="saas_reader",
                    payload={
                        "service": "freee",
                        "operation": "get_employees",
                        "params": {"employee_ids": context["employee_ids"]},
                    },
                    context=context,
                ))
                employees_raw = s2_out.result.get("employees", [])
            except Exception as e2:
                s2_out = MicroAgentOutput(
                    agent_name="employee_data_reader", success=False,
                    result={"error": str(e2)}, confidence=0.0,
                    cost_yen=0.0, duration_ms=int(time.time() * 1000) - t2,
                )

    record_step(2, "employee_data_reader", "saas_reader", s2_out)
    if not s2_out.success:
        return emit_fail("employee_data_reader")
    context["employees_raw"] = employees_raw

    # ─── Step 3: standard_pay_calculator ────────────────────────────────
    t3 = int(time.time() * 1000)
    employees_with_smp: list[dict] = []
    try:
        for emp in employees_raw:
            if resolved_type == "standard_base":
                pays = emp.get("monthly_pays_apr_jun", [])
                avg = int(sum(pays) / len(pays)) if pays else emp.get("monthly_salary", 0)
            elif resolved_type == "monthly_change":
                pays = emp.get("monthly_pays_3m", [])
                avg = int(sum(pays) / len(pays)) if pays else emp.get("monthly_salary", 0)
            elif resolved_type == "bonus":
                avg = emp.get("bonus_amount", 0)
            else:
                avg = emp.get("monthly_salary", 0)

            employees_with_smp.append({
                **emp,
                "average_monthly_salary": avg,
                "standard_monthly_pay": _get_standard_monthly_pay(avg),
            })
        s3_out = MicroAgentOutput(
            agent_name="standard_pay_calculator", success=True,
            result={"employees": employees_with_smp},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t3,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="standard_pay_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t3,
        )

    record_step(3, "standard_pay_calculator", "cost_calculator", s3_out)
    if not s3_out.success:
        return emit_fail("standard_pay_calculator")

    # ─── Step 4: premium_calculator ──────────────────────────────────────
    t4 = int(time.time() * 1000)
    employees_with_premium: list[dict] = []
    try:
        for emp in employees_with_smp:
            smp = emp["standard_monthly_pay"]
            if resolved_type == "bonus":
                smp = min(smp, _BONUS_SMP_CAP)

            health_total = int(Decimal(str(smp)) * _HEALTH_RATE)
            health_emp = health_total // 2
            health_er = health_total - health_emp

            pension_total = int(Decimal(str(smp)) * _PENSION_RATE)
            pension_emp = pension_total // 2
            pension_er = pension_total - pension_emp

            ei_total = int(Decimal(str(smp)) * _EMP_INS_TOTAL_RATE)
            ei_emp = int(Decimal(str(smp)) * _EMP_INS_EMPLOYEE_RATE)
            ei_er = ei_total - ei_emp

            employees_with_premium.append({
                **emp,
                "health_insurance_employee": health_emp,
                "health_insurance_employer": health_er,
                "welfare_pension_employee": pension_emp,
                "welfare_pension_employer": pension_er,
                "employment_insurance_employee": ei_emp,
                "employment_insurance_employer": ei_er,
                "total_premium_employee": health_emp + pension_emp + ei_emp,
                "total_premium_employer": health_er + pension_er + ei_er,
            })
        s4_out = MicroAgentOutput(
            agent_name="premium_calculator", success=True,
            result={"employees": employees_with_premium},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t4,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="premium_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t4,
        )

    record_step(4, "premium_calculator", "cost_calculator", s4_out)
    if not s4_out.success:
        return emit_fail("premium_calculator")

    # ─── Step 5: filing_form_generator ───────────────────────────────────
    filing_deadline = _resolve_filing_deadline(resolved_type, resolved_date)
    days_until = (filing_deadline - today).days

    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": f"社会保険_{FILING_TYPES.get(resolved_type, resolved_type)}",
            "variables": {
                "filing_type": resolved_type,
                "filing_type_label": FILING_TYPES.get(resolved_type, resolved_type),
                "event_date": resolved_date.isoformat(),
                "filing_deadline": filing_deadline.isoformat(),
                "days_until_deadline": days_until,
                "employees": employees_with_premium,
                "employee_count": len(employees_with_premium),
            },
        },
        context=context,
    ))
    record_step(5, "filing_form_generator", "document_generator", gen_out)
    draft_pdf_path = gen_out.result.get("pdf_path", "")

    # ─── Step 6: deadline_compliance ─────────────────────────────────────
    filing_label = FILING_TYPES.get(resolved_type, resolved_type)
    if days_until < 0:
        compliance_alerts.append(
            f"{filing_label}が期限切れ: 期限={filing_deadline.isoformat()}（{abs(days_until)}日超過）"
        )
    elif days_until <= 2:
        compliance_alerts.append(
            f"{filing_label}の期限まで{days_until}日: 今日中に提出が必要です"
        )
    elif days_until <= 5:
        compliance_alerts.append(f"{filing_label}の期限まで残り{days_until}日")

    if resolved_type == "monthly_change":
        for emp in employees_with_premium:
            old_smp = emp.get("old_standard_monthly_pay", 0)
            new_smp = emp.get("standard_monthly_pay", 0)
            if old_smp and new_smp and abs(new_smp - old_smp) / max(old_smp, 1) < 0.02:
                compliance_alerts.append(
                    f"{emp.get('employee_name', emp.get('employee_id', ''))}: "
                    "月額変動が2等級未満の可能性があります（月額変更届の要件を確認してください）"
                )

    comp_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id, agent_name="compliance_checker",
        payload={
            "domain": "social_insurance",
            "filing_type": resolved_type,
            "filing_deadline": filing_deadline.isoformat(),
            "days_until_deadline": days_until,
            "alerts": compliance_alerts,
        },
        context=context,
    ))
    record_step(6, "deadline_compliance", "compliance_checker", comp_out)
    compliance_alerts.extend(comp_out.result.get("additional_alerts", []))

    # ─── Step 7: output_validator ────────────────────────────────────────
    if employees_with_premium:
        emp0 = employees_with_premium[0]
        val_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "employee_id": emp0.get("employee_id", ""),
                    "employee_name": emp0.get("employee_name", ""),
                    "filing_type": resolved_type,
                    "standard_monthly_pay": emp0.get("standard_monthly_pay", 0),
                },
                "required_fields": REQUIRED_FILING_FIELDS,
                "numeric_fields": ["standard_monthly_pay"],
                "positive_fields": ["standard_monthly_pay"],
            },
            context=context,
        ))
    else:
        val_out = MicroAgentOutput(
            agent_name="output_validator", success=False,
            result={"error": "対象従業員データが空です"},
            confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "output_validator", "output_validator", val_out)

    total_cost = steps_cost(steps)
    total_dur = steps_duration(steps, pipeline_start)
    logger.info(
        f"social_insurance_pipeline complete: type={resolved_type}, "
        f"{len(employees_with_premium)}名, deadline={filing_deadline.isoformat()}, {total_dur}ms"
    )

    return SocialInsurancePipelineResult(
        success=val_out.success,
        steps=steps,
        final_output={
            "filing_type": resolved_type,
            "filing_type_label": filing_label,
            "event_date": resolved_date.isoformat(),
            "filing_deadline": filing_deadline.isoformat(),
            "days_until_deadline": days_until,
            "employees": [
                {
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp.get("employee_name"),
                    "filing_type": resolved_type,
                    "standard_monthly_pay": emp.get("standard_monthly_pay", 0),
                    "total_premium_employee": emp.get("total_premium_employee", 0),
                    "total_premium_employer": emp.get("total_premium_employer", 0),
                    "health_insurance_employee": emp.get("health_insurance_employee", 0),
                    "welfare_pension_employee": emp.get("welfare_pension_employee", 0),
                    "employment_insurance_employee": emp.get("employment_insurance_employee", 0),
                }
                for emp in employees_with_premium
            ],
            "employee_count": len(employees_with_premium),
            "draft_pdf_path": draft_pdf_path,
            "egov_ready": val_out.success and days_until >= 0,
            "compliance_alerts": compliance_alerts,
        },
        total_cost_yen=total_cost,
        total_duration_ms=total_dur,
        compliance_alerts=compliance_alerts,
    )

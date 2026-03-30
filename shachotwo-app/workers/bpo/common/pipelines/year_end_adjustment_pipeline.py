"""
共通BPO 年末調整パイプライン（マイクロエージェント版）

Steps:
  Step 1: saas_reader          年間給与データ + 源泉徴収済み税額取得（freee / SmartHR）
  Step 2: extractor            従業員提出書類から控除情報抽出（保険料控除、住宅ローン、扶養）
  Step 3: income_tax_calculator 年税額計算（給与所得控除→基礎控除→各種控除→税率適用）
  Step 4: refund_calculator    過不足税額算出（年税額 - 源泉徴収済み = 還付/追徴）
  Step 5: generator            源泉徴収票ドラフト生成
  Step 6: compliance           配偶者控除・扶養控除の所得制限チェック
  Step 7: validator            計算整合性検証

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション2.4
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
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

# 所得税率表（課税所得額→税率・控除額）
_TAX_BRACKETS = [
    (1_950_000,  Decimal("0.05"),  Decimal("0")),
    (3_300_000,  Decimal("0.10"),  Decimal("97_500")),
    (6_950_000,  Decimal("0.20"),  Decimal("427_500")),
    (9_000_000,  Decimal("0.23"),  Decimal("636_000")),
    (18_000_000, Decimal("0.33"),  Decimal("1_536_000")),
    (40_000_000, Decimal("0.40"),  Decimal("2_796_000")),
    (float("inf"), Decimal("0.45"), Decimal("4_796_000")),
]

# 給与所得控除額計算（2024年度）
def _salary_income_deduction(annual_income: int) -> int:
    if annual_income <= 1_625_000:
        return 550_000
    if annual_income <= 1_800_000:
        return int(annual_income * 0.4) - 100_000
    if annual_income <= 3_600_000:
        return int(annual_income * 0.3) + 80_000
    if annual_income <= 6_600_000:
        return int(annual_income * 0.2) + 440_000
    if annual_income <= 8_500_000:
        return int(annual_income * 0.1) + 1_100_000
    return 1_950_000


def _calc_income_tax(taxable_income: int) -> int:
    """課税所得から所得税額を算出する。"""
    d = Decimal(str(max(taxable_income, 0)))
    for threshold, rate, deduction in _TAX_BRACKETS:
        if d <= Decimal(str(threshold)):
            return int(d * rate - deduction)
    return int(d * Decimal("0.45") - Decimal("4_796_000"))


REQUIRED_WITHHOLDING_FIELDS = [
    "employee_id", "employee_name", "annual_income",
    "income_tax_withheld", "tax_due", "refund_or_additional",
]

# 配偶者控除の所得制限（2024年）
_SPOUSE_DEDUCTION_INCOME_LIMIT = 1_500_000   # 合計所得 150万超→控除額逓減
_DEPENDENT_INCOME_LIMIT = 480_000             # 扶養親族 所得 48万超→扶養除外


@dataclass
class YearEndAdjustmentPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[str] = field(default_factory=list)

    def summary_text(self) -> str:
        extra = [f"  ALERT: {a}" for a in self.compliance_alerts]
        return pipeline_summary(
            label="年末調整パイプライン",
            total_steps=7,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra or None,
        )


async def run_year_end_adjustment_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    target_year: int | None = None,
) -> YearEndAdjustmentPipelineResult:
    """
    年末調整パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            {"target_year": int, "employee_ids": list[str]}
              or
            {"employees": list[dict]}  -- 直渡し（テスト・手動）
        target_year: 計算対象年度（省略時は input_data["target_year"] を使用）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "year_end_adjustment",
    }

    resolved_year = target_year or int(input_data.get("target_year", 2025))
    context["target_year"] = resolved_year

    def _year_end_fail(step_name: str) -> YearEndAdjustmentPipelineResult:
        return YearEndAdjustmentPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=steps_cost(steps),
            total_duration_ms=steps_duration(steps, pipeline_start),
            failed_step=step_name,
            compliance_alerts=compliance_alerts,
        )

    # ─── Step 1: saas_reader（年間給与・源泉徴収済み税額） ─────────────────
    t1 = int(time.time() * 1000)
    employees_raw: list[dict] = []

    if "employees" in input_data:
        employees_raw = input_data["employees"]
        s1_out = MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"employees": employees_raw, "source": "direct"},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t1,
        )
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "get_year_end_payroll",
                    "params": {
                        "year": resolved_year,
                        "employee_ids": input_data.get("employee_ids", []),
                        "include_withheld_tax": True,
                    },
                },
                context=context,
            ))
            employees_raw = s1_out.result.get("employees", [])
        except Exception as e:
            logger.warning(f"freee年末調整取得失敗、SmartHRにフォールバック: {e}")
            try:
                s1_out = await run_saas_reader(MicroAgentInput(
                    company_id=company_id, agent_name="saas_reader",
                    payload={
                        "service": "smarthr",
                        "operation": "get_year_end_payroll",
                        "params": {
                            "year": resolved_year,
                            "employee_ids": input_data.get("employee_ids", []),
                        },
                    },
                    context=context,
                ))
                employees_raw = s1_out.result.get("employees", [])
            except Exception as e2:
                s1_out = MicroAgentOutput(
                    agent_name="saas_reader", success=False,
                    result={"error": str(e2)}, confidence=0.0,
                    cost_yen=0.0, duration_ms=int(time.time() * 1000) - t1,
                )

    record_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _year_end_fail("saas_reader")
    context["employees_raw"] = employees_raw

    # ─── Step 2: extractor（控除情報抽出） ────────────────────────────────
    t2 = int(time.time() * 1000)
    deduction_schema = {
        "employee_id": "str",
        "life_insurance_deduction": "int: 生命保険料控除額",
        "earthquake_insurance_deduction": "int: 地震保険料控除額",
        "housing_loan_deduction": "int: 住宅ローン控除額",
        "spouse_income": "int: 配偶者の合計所得見積額（0なし）",
        "dependents": "list[dict]: [{name, income, relation}]",
        "disability_deduction": "int: 障害者控除額（0なし）",
        "widow_single_parent_deduction": "int: ひとり親・寡婦控除額（0なし）",
        "working_student_deduction": "int: 勤労学生控除額（0なし）",
    }

    if "deduction_text" in input_data:
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": input_data["deduction_text"],
                "schema": deduction_schema,
                "domain": "year_end_adjustment",
            },
            context=context,
        ))
    elif "deduction_data" in input_data:
        s2_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result={"deductions": input_data["deduction_data"]},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t2,
        )
    else:
        # 申告書未提出の場合は基礎控除のみ適用
        s2_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result={"deductions": [], "note": "申告書未提出 — 基礎控除のみ適用"},
            confidence=0.85, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t2,
        )

    record_step(2, "extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _year_end_fail("extractor")

    deductions_by_id: dict[str, dict] = {}
    for d in s2_out.result.get("deductions", []):
        if isinstance(d, dict) and "employee_id" in d:
            deductions_by_id[d["employee_id"]] = d

    # ─── Step 3: income_tax_calculator（年税額計算） ────────────────────────
    t3 = int(time.time() * 1000)
    employees_with_tax: list[dict] = []
    try:
        for emp in employees_raw:
            emp_id = emp.get("employee_id", "")
            annual_income = int(emp.get("annual_income", 0))
            ded = deductions_by_id.get(emp_id, {})

            salary_ded = _salary_income_deduction(annual_income)
            basic_ded = 480_000  # 基礎控除（合計所得2400万以下）

            life_ins_ded = min(int(ded.get("life_insurance_deduction", 0)), 120_000)
            quake_ins_ded = min(int(ded.get("earthquake_insurance_deduction", 0)), 50_000)
            housing_loan_ded = int(ded.get("housing_loan_deduction", 0))

            # 配偶者控除 (最大38万、配偶者所得38万以下)
            spouse_income = int(ded.get("spouse_income", -1))
            spouse_ded = 0
            if 0 <= spouse_income <= 380_000:
                spouse_ded = 380_000
            elif 380_000 < spouse_income <= _SPOUSE_DEDUCTION_INCOME_LIMIT:
                spouse_ded = 260_000  # 配偶者特別控除（簡易計算）

            # 扶養控除（1名=38万、特定=63万、老親=58万）
            dependents_ded = 0
            for dep in ded.get("dependents", []):
                dep_income = int(dep.get("income", 0))
                if dep_income <= _DEPENDENT_INCOME_LIMIT:
                    dependents_ded += 380_000  # 一般扶養（簡易計算）

            disability_ded = int(ded.get("disability_deduction", 0))
            widow_ded = int(ded.get("widow_single_parent_deduction", 0))
            student_ded = int(ded.get("working_student_deduction", 0))

            total_deductions = (
                salary_ded + basic_ded + life_ins_ded + quake_ins_ded
                + housing_loan_ded + spouse_ded + dependents_ded
                + disability_ded + widow_ded + student_ded
            )
            taxable_income = max(annual_income - total_deductions, 0)
            income_tax = _calc_income_tax(taxable_income)
            # 復興特別所得税（2.1%加算）
            reconstruction_tax = int(income_tax * 0.021)
            tax_due = income_tax + reconstruction_tax

            employees_with_tax.append({
                **emp,
                "salary_income_deduction": salary_ded,
                "basic_deduction": basic_ded,
                "life_insurance_deduction": life_ins_ded,
                "earthquake_insurance_deduction": quake_ins_ded,
                "housing_loan_deduction": housing_loan_ded,
                "spouse_deduction": spouse_ded,
                "dependent_deduction": dependents_ded,
                "total_deductions": total_deductions,
                "taxable_income": taxable_income,
                "income_tax": income_tax,
                "reconstruction_tax": reconstruction_tax,
                "tax_due": tax_due,
            })

        s3_out = MicroAgentOutput(
            agent_name="income_tax_calculator", success=True,
            result={"employees": employees_with_tax},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t3,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="income_tax_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t3,
        )

    record_step(3, "income_tax_calculator", "cost_calculator", s3_out)
    if not s3_out.success:
        return _year_end_fail("income_tax_calculator")

    # ─── Step 4: refund_calculator（過不足税額） ────────────────────────────
    t4 = int(time.time() * 1000)
    employees_with_refund: list[dict] = []
    total_refund = Decimal("0")
    total_additional = Decimal("0")
    try:
        for emp in employees_with_tax:
            tax_due = emp["tax_due"]
            tax_withheld = int(emp.get("income_tax_withheld", 0))
            diff = tax_due - tax_withheld  # 正→追徴、負→還付
            if diff < 0:
                total_refund += Decimal(str(abs(diff)))
            else:
                total_additional += Decimal(str(diff))

            employees_with_refund.append({
                **emp,
                "refund_or_additional": diff,
                "settlement_type": "追徴" if diff > 0 else ("還付" if diff < 0 else "過不足なし"),
            })

        s4_out = MicroAgentOutput(
            agent_name="refund_calculator", success=True,
            result={
                "employees": employees_with_refund,
                "total_refund": float(total_refund),
                "total_additional": float(total_additional),
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t4,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="refund_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t4,
        )

    record_step(4, "refund_calculator", "cost_calculator", s4_out)
    if not s4_out.success:
        return _year_end_fail("refund_calculator")

    # ─── Step 5: generator（源泉徴収票ドラフト） ────────────────────────────
    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "源泉徴収票",
            "variables": {
                "target_year": resolved_year,
                "employees": employees_with_refund,
                "total_refund": float(total_refund),
                "total_additional": float(total_additional),
                "employee_count": len(employees_with_refund),
            },
        },
        context=context,
    ))
    record_step(5, "filing_form_generator", "document_generator", gen_out)
    withholding_slips = gen_out.result.get("slips", [])

    # ─── Step 6: compliance（所得制限チェック） ──────────────────────────────
    for emp in employees_with_refund:
        emp_name = emp.get("employee_name", emp.get("employee_id", ""))
        ded = deductions_by_id.get(emp.get("employee_id", ""), {})

        spouse_income = int(ded.get("spouse_income", -1))
        if 0 <= spouse_income > _SPOUSE_DEDUCTION_INCOME_LIMIT:
            compliance_alerts.append(
                f"{emp_name}: 配偶者の所得が{spouse_income:,}円で所得制限超過。"
                "配偶者控除/特別控除の適用を再確認してください"
            )

        for dep in ded.get("dependents", []):
            dep_income = int(dep.get("income", 0))
            dep_name = dep.get("name", "扶養者")
            if dep_income > _DEPENDENT_INCOME_LIMIT:
                compliance_alerts.append(
                    f"{emp_name}: 扶養親族「{dep_name}」の所得{dep_income:,}円が"
                    f"扶養控除の所得制限（{_DEPENDENT_INCOME_LIMIT:,}円）を超えています"
                )

    comp_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id, agent_name="compliance_checker",
        payload={
            "domain": "year_end_adjustment",
            "year": resolved_year,
            "alerts": compliance_alerts,
            "employee_count": len(employees_with_refund),
            "total_refund": float(total_refund),
        },
        context=context,
    ))
    record_step(6, "compliance", "compliance_checker", comp_out)
    compliance_alerts.extend(comp_out.result.get("additional_alerts", []))

    # ─── Step 7: validator（計算整合性検証） ────────────────────────────────
    if employees_with_refund:
        emp0 = employees_with_refund[0]
        val_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "employee_id": emp0.get("employee_id", ""),
                    "employee_name": emp0.get("employee_name", ""),
                    "annual_income": emp0.get("annual_income", 0),
                    "income_tax_withheld": emp0.get("income_tax_withheld", 0),
                    "tax_due": emp0.get("tax_due", 0),
                    "refund_or_additional": emp0.get("refund_or_additional", 0),
                },
                "required_fields": REQUIRED_WITHHOLDING_FIELDS,
                "numeric_fields": ["annual_income", "income_tax_withheld", "tax_due"],
                "positive_fields": ["annual_income"],
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
        f"year_end_adjustment_pipeline complete: year={resolved_year}, "
        f"{len(employees_with_refund)}名, refund=¥{total_refund}, {total_dur}ms"
    )

    return YearEndAdjustmentPipelineResult(
        success=val_out.success,
        steps=steps,
        final_output={
            "target_year": resolved_year,
            "employees": [
                {
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp.get("employee_name"),
                    "annual_income": emp.get("annual_income", 0),
                    "total_deductions": emp.get("total_deductions", 0),
                    "taxable_income": emp.get("taxable_income", 0),
                    "tax_due": emp.get("tax_due", 0),
                    "income_tax_withheld": emp.get("income_tax_withheld", 0),
                    "refund_or_additional": emp.get("refund_or_additional", 0),
                    "settlement_type": emp.get("settlement_type", ""),
                }
                for emp in employees_with_refund
            ],
            "employee_count": len(employees_with_refund),
            "total_refund": float(total_refund),
            "total_additional": float(total_additional),
            "withholding_slips": withholding_slips,
            "compliance_alerts": compliance_alerts,
        },
        total_cost_yen=total_cost,
        total_duration_ms=total_dur,
        compliance_alerts=compliance_alerts,
    )

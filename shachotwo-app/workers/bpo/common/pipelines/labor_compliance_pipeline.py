"""
共通BPO 労務コンプライアンス統合パイプライン（マイクロエージェント版）

Steps:
  Step 1: saas_reader              勤怠・有給データ取得（SmartHR / freee / 直渡し）
  Step 2: overtime_calculator      残業時間集計（月次・年次・特別条項有無判定）
  Step 3: paid_leave_tracker       有給取得状況集計（取得義務5日チェック）
  Step 4: compliance_checker       36協定上限チェック・有給義務チェック・就業規則整合性
  Step 5: anomaly_detector         異常値検知（突発的な残業急増・有給消滅等）
  Step 6: alert_generator          アラート・是正勧告ドラフト生成
  Step 7: output_validator         出力整合性検証

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション2.5

労働基準法準拠:
  - 36協定 一般条項: 月45h / 年360h
  - 36協定 特別条項: 月100h / 年720h（単月上限）、年6回まで
  - 有給取得義務: 年5日以上（労基法第39条第7項）
  - 時間外上限: 月100h未満（複数月平均80h以下）
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.micro.compliance import run_compliance_checker
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    steps_cost,
    steps_duration,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# ─── 36協定 法定上限定数 ────────────────────────────────────────────────────

# 一般条項上限
_OT_MONTHLY_GENERAL_LIMIT = 45     # 月45時間
_OT_ANNUAL_GENERAL_LIMIT = 360     # 年360時間

# 特別条項上限
_OT_MONTHLY_SPECIAL_LIMIT = 100    # 単月100時間未満（実際は100h未満なので99.9h）
_OT_ANNUAL_SPECIAL_LIMIT = 720     # 年720時間
_OT_SPECIAL_MONTH_MAX_COUNT = 6    # 年6回まで

# 複数月平均上限（2〜6ヶ月平均）
_OT_MULTI_MONTH_AVG_LIMIT = 80     # 80時間以下

# 有給取得義務
_PAID_LEAVE_MIN_DAYS = 5           # 年5日以上

# アラートしきい値（警告を出す残り月数）
_PAID_LEAVE_ALERT_REMAINING_MONTHS = 3


def _calc_overtime_hours(employee: dict[str, Any], month_key: str) -> float:
    """
    1名・1ヶ月の残業時間を返す。

    employee dict のキー候補:
      - monthly_overtime[month_key]: 月別残業時間 dict
      - overtime_hours: 単月の残業時間（月が特定されている場合）
    """
    monthly = employee.get("monthly_overtime", {})
    if month_key and month_key in monthly:
        return float(monthly[month_key])
    return float(employee.get("overtime_hours", 0.0))


def _calc_annual_overtime(employee: dict[str, Any]) -> float:
    """年間残業時間合計を返す。"""
    monthly = employee.get("monthly_overtime", {})
    if monthly:
        return sum(float(v) for v in monthly.values())
    return float(employee.get("annual_overtime_hours", 0.0))


def _count_special_clause_months(employee: dict[str, Any]) -> int:
    """特別条項が適用された月数（月45h超の月数）を返す。"""
    monthly = employee.get("monthly_overtime", {})
    if not monthly:
        return 0
    return sum(1 for v in monthly.values() if float(v) > _OT_MONTHLY_GENERAL_LIMIT)


def _calc_multi_month_avg(employee: dict[str, Any], n_months: int = 2) -> float:
    """直近 n_months ヶ月の残業時間平均を返す。"""
    monthly = employee.get("monthly_overtime", {})
    if not monthly:
        return 0.0
    sorted_vals = [float(v) for v in sorted(monthly.values(), reverse=True)]
    window = sorted_vals[:n_months]
    return sum(window) / len(window) if window else 0.0


def _count_paid_leave_taken(employee: dict[str, Any]) -> float:
    """年度内の有給取得日数を返す。"""
    return float(employee.get("paid_leave_taken_days", 0.0))


def _resolve_fiscal_year_remaining_months(today: date, fiscal_year_end_month: int = 3) -> int:
    """
    当該会計年度の残り月数を返す。
    fiscal_year_end_month: 年度末月（デフォルト3月 = 3月末締め）
    """
    end_year = today.year if today.month <= fiscal_year_end_month else today.year + 1
    end_date = date(end_year, fiscal_year_end_month, 28)
    delta_months = (end_date.year - today.year) * 12 + (end_date.month - today.month)
    return max(0, delta_months)


@dataclass
class LaborCompliancePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[dict[str, Any]] = field(default_factory=list)

    def labor_summary_text(self) -> str:
        extra = []
        for alert in self.compliance_alerts:
            sev = alert.get("severity", "info").upper()
            extra.append(f"  [{sev}] {alert.get('message', '')}")
        return pipeline_summary(
            label="労務コンプライアンス統合パイプライン",
            total_steps=7,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra or None,
        )


async def run_labor_compliance_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    target_month: str | None = None,
    fiscal_year_end_month: int = 3,
    has_special_clause: bool = False,
) -> LaborCompliancePipelineResult:
    """
    労務コンプライアンス統合パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            {"target_month": "YYYY-MM", "employee_ids": list[str]}
              or
            {"employees": list[dict]}  -- 直渡し（テスト・手動）
        target_month: チェック対象月（YYYY-MM）。省略時は input_data から取得
        fiscal_year_end_month: 会計年度末月（デフォルト3 = 3月末）
        has_special_clause: 36協定 特別条項が締結されているか

    各 employee dict の期待キー:
        employee_id (str)
        employee_name (str)
        monthly_overtime (dict[str, float]):  {"2025-01": 42.5, ...}
        annual_overtime_hours (float): 年間残業時間合計（monthly_overtime がない場合）
        paid_leave_taken_days (float): 年度内有給取得日数
        paid_leave_granted_days (float): 年度内有給付与日数
        work_rule_violation (bool, optional): 就業規則違反フラグ
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    compliance_alerts: list[dict[str, Any]] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "labor_compliance",
        "today": today.isoformat(),
        "has_special_clause": has_special_clause,
    }

    resolved_month = target_month or str(input_data.get("target_month", today.strftime("%Y-%m")))
    context["target_month"] = resolved_month

    def _labor_compliance_fail(step_name: str) -> LaborCompliancePipelineResult:
        return LaborCompliancePipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=steps_cost(steps),
            total_duration_ms=steps_duration(steps, pipeline_start),
            failed_step=step_name,
            compliance_alerts=compliance_alerts,
        )

    # ─── Step 1: saas_reader（勤怠・有給データ取得） ─────────────────────────
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
                    "service": "smarthr",
                    "operation": "get_attendance",
                    "params": {
                        "month": resolved_month,
                        "employee_ids": input_data.get("employee_ids", []),
                        "include_overtime": True,
                        "include_paid_leave": True,
                    },
                },
                context=context,
            ))
            employees_raw = s1_out.result.get("employees", [])
        except Exception as e:
            logger.warning(f"SmartHR勤怠取得失敗、freeeにフォールバック: {e}")
            try:
                s1_out = await run_saas_reader(MicroAgentInput(
                    company_id=company_id, agent_name="saas_reader",
                    payload={
                        "service": "freee",
                        "operation": "get_attendance",
                        "params": {
                            "month": resolved_month,
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
        return _labor_compliance_fail("saas_reader")
    context["employees_raw"] = employees_raw

    # ─── Step 2: overtime_calculator（残業時間集計） ─────────────────────────
    t2 = int(time.time() * 1000)
    employees_with_ot: list[dict] = []
    try:
        for emp in employees_raw:
            monthly_ot = _calc_overtime_hours(emp, resolved_month)
            annual_ot = _calc_annual_overtime(emp)
            special_months = _count_special_clause_months(emp)
            avg_2m = _calc_multi_month_avg(emp, n_months=2)
            avg_3m = _calc_multi_month_avg(emp, n_months=3)

            employees_with_ot.append({
                **emp,
                "monthly_overtime_hours": monthly_ot,
                "annual_overtime_hours": annual_ot,
                "special_clause_month_count": special_months,
                "avg_overtime_2months": avg_2m,
                "avg_overtime_3months": avg_3m,
            })

        s2_out = MicroAgentOutput(
            agent_name="overtime_calculator", success=True,
            result={
                "employees": employees_with_ot,
                "target_month": resolved_month,
                "has_special_clause": has_special_clause,
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t2,
        )
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="overtime_calculator", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t2,
        )

    record_step(2, "overtime_calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _labor_compliance_fail("overtime_calculator")

    # ─── Step 3: paid_leave_tracker（有給取得状況集計） ──────────────────────
    t3 = int(time.time() * 1000)
    remaining_months = _resolve_fiscal_year_remaining_months(today, fiscal_year_end_month)
    employees_with_leave: list[dict] = []
    try:
        for emp in employees_with_ot:
            taken = _count_paid_leave_taken(emp)
            granted = float(emp.get("paid_leave_granted_days", 10.0))
            obligation_gap = max(0.0, _PAID_LEAVE_MIN_DAYS - taken)  # 義務未達日数

            employees_with_leave.append({
                **emp,
                "paid_leave_taken_days": taken,
                "paid_leave_granted_days": granted,
                "paid_leave_obligation_gap": obligation_gap,
                "paid_leave_remaining_months": remaining_months,
                "paid_leave_obligation_met": obligation_gap <= 0,
            })

        s3_out = MicroAgentOutput(
            agent_name="paid_leave_tracker", success=True,
            result={
                "employees": employees_with_leave,
                "fiscal_year_remaining_months": remaining_months,
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t3,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="paid_leave_tracker", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - t3,
        )

    record_step(3, "paid_leave_tracker", "cost_calculator", s3_out)
    if not s3_out.success:
        return _labor_compliance_fail("paid_leave_tracker")

    # ─── Step 4: compliance_checker（36協定・有給義務・就業規則チェック） ────
    t4 = int(time.time() * 1000)
    for emp in employees_with_leave:
        emp_name = emp.get("employee_name", emp.get("employee_id", "（不明）"))
        monthly_ot = emp["monthly_overtime_hours"]
        annual_ot = emp["annual_overtime_hours"]
        special_count = emp["special_clause_month_count"]
        avg_2m = emp["avg_overtime_2months"]
        gap = emp["paid_leave_obligation_gap"]

        # 36協定チェック（一般条項）
        if monthly_ot > _OT_MONTHLY_GENERAL_LIMIT and not has_special_clause:
            compliance_alerts.append({
                "type": "overtime_limit_general",
                "severity": "high",
                "employee_id": emp.get("employee_id"),
                "employee_name": emp_name,
                "message": (
                    f"{emp_name}: 月残業{monthly_ot:.1f}h が一般条項上限"
                    f"（{_OT_MONTHLY_GENERAL_LIMIT}h）を超えています。"
                    "特別条項の締結または業務量の削減が必要です。"
                ),
            })
        elif monthly_ot >= _OT_MONTHLY_GENERAL_LIMIT * 0.9 and not has_special_clause:
            # 警告水準（上限の90%以上）
            compliance_alerts.append({
                "type": "overtime_warning_general",
                "severity": "medium",
                "employee_id": emp.get("employee_id"),
                "employee_name": emp_name,
                "message": (
                    f"{emp_name}: 月残業{monthly_ot:.1f}h が一般条項上限"
                    f"（{_OT_MONTHLY_GENERAL_LIMIT}h）の90%に達しています。"
                ),
            })

        # 36協定チェック（特別条項）
        if has_special_clause:
            if monthly_ot >= _OT_MONTHLY_SPECIAL_LIMIT:
                compliance_alerts.append({
                    "type": "overtime_limit_special_monthly",
                    "severity": "critical",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 月残業{monthly_ot:.1f}h が特別条項の単月上限"
                        f"（{_OT_MONTHLY_SPECIAL_LIMIT}h未満）に抵触しています。"
                        "労基法第36条違反となります。"
                    ),
                })
            if special_count > _OT_SPECIAL_MONTH_MAX_COUNT:
                compliance_alerts.append({
                    "type": "overtime_limit_special_count",
                    "severity": "high",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 特別条項適用月数が{special_count}回で"
                        f"年上限（{_OT_SPECIAL_MONTH_MAX_COUNT}回）を超えています。"
                    ),
                })
            if annual_ot > _OT_ANNUAL_SPECIAL_LIMIT:
                compliance_alerts.append({
                    "type": "overtime_limit_annual_special",
                    "severity": "critical",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 年間残業{annual_ot:.1f}h が特別条項の年上限"
                        f"（{_OT_ANNUAL_SPECIAL_LIMIT}h）を超えています。"
                    ),
                })
        else:
            if annual_ot > _OT_ANNUAL_GENERAL_LIMIT:
                compliance_alerts.append({
                    "type": "overtime_limit_annual_general",
                    "severity": "high",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 年間残業{annual_ot:.1f}h が一般条項の年上限"
                        f"（{_OT_ANNUAL_GENERAL_LIMIT}h）を超えています。"
                    ),
                })

        # 複数月平均上限チェック（2ヶ月平均80h）
        if avg_2m > _OT_MULTI_MONTH_AVG_LIMIT:
            compliance_alerts.append({
                "type": "overtime_multi_month_avg",
                "severity": "high",
                "employee_id": emp.get("employee_id"),
                "employee_name": emp_name,
                "message": (
                    f"{emp_name}: 直近2ヶ月平均残業{avg_2m:.1f}h が"
                    f"複数月平均上限（{_OT_MULTI_MONTH_AVG_LIMIT}h）を超えています。"
                    "過労死ラインに達しています。"
                ),
            })

        # 有給取得義務チェック（年5日）
        if gap > 0:
            if remaining_months <= _PAID_LEAVE_ALERT_REMAINING_MONTHS:
                compliance_alerts.append({
                    "type": "paid_leave_obligation",
                    "severity": "critical" if remaining_months <= 1 else "high",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 有給取得義務まであと{gap:.0f}日不足。"
                        f"年度末まで残り{remaining_months}ヶ月。"
                        "計画的付与の実施が必要です（労基法第39条第7項）。"
                    ),
                })
            else:
                compliance_alerts.append({
                    "type": "paid_leave_obligation_warning",
                    "severity": "medium",
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp_name,
                    "message": (
                        f"{emp_name}: 有給取得義務まであと{gap:.0f}日不足。"
                        f"年度末まで{remaining_months}ヶ月あります。"
                    ),
                })

        # 就業規則違反フラグ
        if emp.get("work_rule_violation"):
            compliance_alerts.append({
                "type": "work_rule_violation",
                "severity": "high",
                "employee_id": emp.get("employee_id"),
                "employee_name": emp_name,
                "message": f"{emp_name}: 就業規則違反が検知されています。",
            })

    comp_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id, agent_name="compliance_checker",
        payload={
            "data": {
                "employee_count": len(employees_with_leave),
                "has_special_clause": has_special_clause,
                "alert_count": len(compliance_alerts),
            },
            "industry": "common",
        },
        context=context,
    ))
    record_step(4, "compliance_checker", "compliance_checker", comp_out)

    # ─── Step 5: anomaly_detector（残業急増・異常値検知） ────────────────────
    t5 = int(time.time() * 1000)
    anomaly_items: list[dict] = []
    for emp in employees_with_leave:
        monthly_ot = emp["monthly_overtime_hours"]
        emp_name = emp.get("employee_name", emp.get("employee_id", ""))
        # 月残業時間の合理的範囲: 0〜80h（80h超は過労死ライン）
        anomaly_items.append({
            "name": f"{emp_name}_月残業",
            "value": monthly_ot,
            "expected_range": [0, 80],
        })
        paid_leave = emp["paid_leave_taken_days"]
        granted = emp["paid_leave_granted_days"]
        if granted > 0:
            anomaly_items.append({
                "name": f"{emp_name}_有給取得率",
                "value": round(paid_leave / granted * 100, 1),
                "expected_range": [0, 100],
            })

    if anomaly_items:
        anom_out = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id, agent_name="anomaly_detector",
            payload={
                "items": anomaly_items,
                "detect_modes": ["range", "zscore"],
                "historical_values": input_data.get("historical_overtime", {}),
            },
            context=context,
        ))
    else:
        anom_out = MicroAgentOutput(
            agent_name="anomaly_detector", success=True,
            result={"anomalies": [], "total_checked": 0, "anomaly_count": 0, "passed": True},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - t5,
        )

    record_step(5, "anomaly_detector", "anomaly_detector", anom_out)

    # 異常値をコンプライアンスアラートにも追記
    for anomaly in anom_out.result.get("anomalies", []):
        if anomaly.get("severity") in ("high", "medium"):
            compliance_alerts.append({
                "type": "anomaly_detected",
                "severity": anomaly["severity"],
                "message": f"[異常値] {anomaly['message']}",
            })

    # ─── Step 6: alert_generator（是正勧告ドラフト生成） ─────────────────────
    critical_alerts = [a for a in compliance_alerts if a.get("severity") in ("critical", "high")]
    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "労務コンプライアンス_是正勧告",
            "variables": {
                "target_month": resolved_month,
                "employee_count": len(employees_with_leave),
                "total_alerts": len(compliance_alerts),
                "critical_alert_count": len(critical_alerts),
                "has_special_clause": has_special_clause,
                "alerts": compliance_alerts,
                "summary": {
                    "overtime_violations": sum(
                        1 for a in compliance_alerts
                        if "overtime" in a.get("type", "")
                    ),
                    "paid_leave_violations": sum(
                        1 for a in compliance_alerts
                        if "paid_leave" in a.get("type", "")
                    ),
                },
            },
        },
        context=context,
    ))
    record_step(6, "alert_generator", "document_generator", gen_out)
    report_path = gen_out.result.get("pdf_path", "")

    # ─── Step 7: output_validator（出力整合性検証） ──────────────────────────
    if employees_with_leave:
        emp0 = employees_with_leave[0]
        val_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "employee_id": emp0.get("employee_id", ""),
                    "employee_name": emp0.get("employee_name", ""),
                    "monthly_overtime_hours": emp0.get("monthly_overtime_hours", 0.0),
                    "paid_leave_taken_days": emp0.get("paid_leave_taken_days", 0.0),
                    "paid_leave_obligation_gap": emp0.get("paid_leave_obligation_gap", 0.0),
                },
                "required_fields": [
                    "employee_id", "employee_name",
                    "monthly_overtime_hours", "paid_leave_taken_days",
                ],
                "numeric_fields": [
                    "monthly_overtime_hours", "paid_leave_taken_days",
                    "paid_leave_obligation_gap",
                ],
                "positive_fields": [
                    "monthly_overtime_hours", "paid_leave_taken_days",
                ],
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
    critical_count = len([a for a in compliance_alerts if a.get("severity") in ("critical", "high")])

    logger.info(
        f"labor_compliance_pipeline complete: month={resolved_month}, "
        f"{len(employees_with_leave)}名, alerts={len(compliance_alerts)}"
        f"(critical/high={critical_count}), {total_dur}ms"
    )

    return LaborCompliancePipelineResult(
        success=val_out.success,
        steps=steps,
        final_output={
            "target_month": resolved_month,
            "has_special_clause": has_special_clause,
            "fiscal_year_remaining_months": remaining_months,
            "employee_count": len(employees_with_leave),
            "employees": [
                {
                    "employee_id": emp.get("employee_id"),
                    "employee_name": emp.get("employee_name"),
                    "monthly_overtime_hours": emp.get("monthly_overtime_hours", 0.0),
                    "annual_overtime_hours": emp.get("annual_overtime_hours", 0.0),
                    "special_clause_month_count": emp.get("special_clause_month_count", 0),
                    "avg_overtime_2months": emp.get("avg_overtime_2months", 0.0),
                    "paid_leave_taken_days": emp.get("paid_leave_taken_days", 0.0),
                    "paid_leave_granted_days": emp.get("paid_leave_granted_days", 0.0),
                    "paid_leave_obligation_met": emp.get("paid_leave_obligation_met", False),
                    "paid_leave_obligation_gap": emp.get("paid_leave_obligation_gap", 0.0),
                }
                for emp in employees_with_leave
            ],
            "compliance_alerts": compliance_alerts,
            "alert_count": len(compliance_alerts),
            "critical_alert_count": critical_count,
            "report_path": report_path,
            "anomaly_count": anom_out.result.get("anomaly_count", 0),
        },
        total_cost_yen=total_cost,
        total_duration_ms=total_dur,
        compliance_alerts=compliance_alerts,
    )

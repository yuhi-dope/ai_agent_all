"""物流・運送業 届出・許認可管理パイプライン

Steps:
  Step 1: extractor           許認可・届出データ構造化（事業者情報・許可番号・期限）
  Step 2: deadline_checker    各種期限チェック（事業報告書7/10期限・実績報告書7/31・更新申請）
  Step 3: rule_matcher        貨物自動車運送事業法チェック（車両台数・営業所要件・事業区域）
  Step 4: report_generator    事業報告書・実績報告書・変更届出書類生成
  Step 5: compliance_checker  提出物完全性チェック（必須添付書類・記載事項）
  Step 6: validator           提出前最終バリデーション（押印欄・有効期限・金額整合）
  Step 7: saas_writer         execution_logs保存 + 期限アラート通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 法定届出期限
BUSINESS_REPORT_DEADLINE = "07-10"      # 事業報告書（毎年7月10日）
PERFORMANCE_REPORT_DEADLINE = "07-31"   # 実績報告書（毎年7月31日）

# 期限アラート閾値（日）
DEADLINE_ALERT_DAYS = 60
DEADLINE_CRITICAL_DAYS = 14

# 一般貨物自動車運送事業：最低車両台数
MIN_VEHICLES = 5

# 必要な届出種別
PERMIT_TYPES = {
    "business_report": "事業報告書",
    "performance_report": "実績報告書",
    "vehicle_change": "事業用自動車の増減届",
    "office_change": "営業所変更届",
    "driver_change": "運行管理者変更届",
    "renewal": "許可更新申請",
}


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
class PermitManagementResult:
    """届出・許認可管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 届出・許認可管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
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


async def run_permit_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> PermitManagementResult:
    """
    届出・許認可管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "fiscal_year": int,             # 対象事業年度
            "company_name": str,
            "permit_no": str,               # 許可番号
            "permit_office": str,           # 許可行政庁（陸運局）
            "vehicle_count": int,           # 保有車両台数
            "office_count": int,            # 営業所数
            "driver_count": int,            # ドライバー数
            "annual_revenue_yen": float,    # 年間売上高
            "annual_transport_km": float,   # 年間輸送距離（km）
            "annual_tonnage": float,        # 年間輸送重量（t）
            "pending_filings": list[{
                "filing_type": str,         # business_report/performance_report等
                "deadline": str,            # 提出期限 YYYY-MM-DD
                "status": str,             # pending/filed/overdue
            }],
            "changes": list[{
                "change_type": str,         # vehicle_change/office_change/driver_change
                "change_date": str,         # 変更日 YYYY-MM-DD
                "description": str,
            }],
        }

    Returns:
        PermitManagementResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {"company_id": company_id}

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
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

    def _fail(step_name: str) -> PermitManagementResult:
        return PermitManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    pending_filings = input_data.get("pending_filings", [])
    changes = input_data.get("changes", [])

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "fiscal_year": "int",
                "company_name": "string",
                "permit_no": "string",
                "vehicle_count": "int",
                "pending_filings": "list",
                "changes": "list",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: deadline_checker（期限チェック）────────────────────────
    s2_start = int(time.time() * 1000)
    today = date.today()
    deadline_alerts: list[dict[str, Any]] = []

    # 法定届出期限の自動生成（当年度）
    current_year = today.year
    auto_deadlines = [
        {
            "filing_type": "business_report",
            "label": "事業報告書",
            "deadline": f"{current_year}-07-10",
        },
        {
            "filing_type": "performance_report",
            "label": "実績報告書",
            "deadline": f"{current_year}-07-31",
        },
    ]

    all_filings = auto_deadlines + [
        {
            "filing_type": f.get("filing_type", ""),
            "label": PERMIT_TYPES.get(f.get("filing_type", ""), f.get("filing_type", "")),
            "deadline": f.get("deadline", ""),
            "status": f.get("status", "pending"),
        }
        for f in pending_filings
    ]

    for filing in all_filings:
        deadline_str = filing.get("deadline", "")
        if not deadline_str:
            continue
        try:
            deadline = date.fromisoformat(deadline_str)
            days_left = (deadline - today).days
            if days_left < 0:
                status = "overdue"
            elif days_left <= DEADLINE_CRITICAL_DAYS:
                status = "critical"
            elif days_left <= DEADLINE_ALERT_DAYS:
                status = "warning"
            else:
                status = "ok"
            if status in ("overdue", "critical", "warning"):
                deadline_alerts.append({
                    "filing_type": filing.get("filing_type", ""),
                    "label": filing.get("label", ""),
                    "deadline": deadline_str,
                    "days_left": days_left,
                    "status": status,
                })
        except ValueError:
            pass

    s2_out = MicroAgentOutput(
        agent_name="deadline_checker",
        success=True,
        result={
            "deadline_alerts": deadline_alerts,
            "overdue_count": sum(1 for a in deadline_alerts if a["status"] == "overdue"),
            "critical_count": sum(1 for a in deadline_alerts if a["status"] == "critical"),
            "warning_count": sum(1 for a in deadline_alerts if a["status"] == "warning"),
            "all_filings": all_filings,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "deadline_checker", "deadline_checker", s2_out)
    context["deadline_result"] = s2_out.result

    # ─── Step 3: rule_matcher（貨物自動車運送事業法チェック）─────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "cargo_transport_business_law",
            "items": [
                {
                    "vehicle_count": input_data.get("vehicle_count", 0),
                    "min_vehicles": MIN_VEHICLES,
                    "office_count": input_data.get("office_count", 0),
                    "driver_count": input_data.get("driver_count", 0),
                    "permit_no": input_data.get("permit_no", ""),
                    "changes": changes,
                }
            ],
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    context["law_compliance"] = s3_out.result

    # ─── Step 4: report_generator（各種届出書類生成）────────────────────
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "事業報告書（一般貨物）",
            "variables": {
                "fiscal_year": input_data.get("fiscal_year", current_year - 1),
                "company_name": input_data.get("company_name", ""),
                "permit_no": input_data.get("permit_no", ""),
                "permit_office": input_data.get("permit_office", ""),
                "vehicle_count": input_data.get("vehicle_count", 0),
                "office_count": input_data.get("office_count", 0),
                "driver_count": input_data.get("driver_count", 0),
                "annual_revenue_yen": input_data.get("annual_revenue_yen", 0.0),
                "annual_transport_km": input_data.get("annual_transport_km", 0.0),
                "annual_tonnage": input_data.get("annual_tonnage", 0.0),
                "changes": changes,
            },
        },
        context=context,
    ))
    _add_step(4, "report_generator", "document_generator", s4_out)
    context["generated_doc"] = s4_out.result

    # ─── Step 5: compliance_checker（提出物完全性チェック）──────────────
    required_fields = [
        "fiscal_year", "company_name", "permit_no", "vehicle_count",
        "annual_revenue_yen", "annual_transport_km",
    ]
    missing_fields = [f for f in required_fields if not input_data.get(f)]

    s5_start = int(time.time() * 1000)
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "missing_fields": missing_fields,
            "passed": len(missing_fields) == 0,
            "vehicle_count_ok": input_data.get("vehicle_count", 0) >= MIN_VEHICLES,
            "pending_changes_count": len(changes),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)

    # ─── Step 6: validator（提出前最終バリデーション）──────────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "company_name": input_data.get("company_name", ""),
                "permit_no": input_data.get("permit_no", ""),
                "fiscal_year": input_data.get("fiscal_year", ""),
                "generated_doc": s4_out.result,
            },
            "required_fields": ["company_name", "permit_no", "fiscal_year"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", s6_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    overdue_count = s2_out.result.get("overdue_count", 0)
    logger.info(
        f"permit_management_pipeline: company_id={company_id}, "
        f"permit_no={input_data.get('permit_no', '')}, "
        f"overdue={overdue_count}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 期限超過時はSlack緊急通知
            "overdue_filing_count": overdue_count,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "deadline_alerts": deadline_alerts,
        "law_compliance": s3_out.result,
        "generated_doc": s4_out.result,
        "compliance_check": s5_out.result,
        "validation": s6_out.result,
    }

    return PermitManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

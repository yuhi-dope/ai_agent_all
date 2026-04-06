"""
共通BPO 期限リマインダパイプライン（マイクロエージェント版）

Steps:
  Step 1: deadline_scanner    期限データスキャン（直渡し or 複数ソース）
  Step 2: priority_sorter     優先度付け（期限切れ→7日以内→30日以内→それ以降）
  Step 3: reminder_generator  リマインダ生成（run_document_generator使用）
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

PRIORITY_OVERDUE = "overdue"   # 期限切れ
PRIORITY_URGENT = "urgent"     # 7日以内
PRIORITY_WARNING = "warning"   # 30日以内
PRIORITY_NORMAL = "normal"     # それ以降

DEADLINE_TYPES = [
    "建設業許可更新", "労働保険申告", "社会保険算定", "法人税申告",
    "消費税申告", "年末調整", "有価証券報告書", "雇用保険更新",
    "リース契約更新", "保険証書更新", "賃貸契約更新",
]

# 優先度の表示順（ソート用）
_PRIORITY_ORDER = {
    PRIORITY_OVERDUE: 0,
    PRIORITY_URGENT: 1,
    PRIORITY_WARNING: 2,
    PRIORITY_NORMAL: 3,
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
class AdminReminderPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    reminders: list[dict] = field(default_factory=list)  # 優先度別リマインダ一覧


def _calc_priority(deadline_date: date, reference_date: date) -> str:
    """期限日と基準日からpriorityを計算する。"""
    delta = (deadline_date - reference_date).days
    if delta < 0:
        return PRIORITY_OVERDUE
    elif delta <= 7:
        return PRIORITY_URGENT
    elif delta <= 30:
        return PRIORITY_WARNING
    else:
        return PRIORITY_NORMAL


async def run_admin_reminder_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> AdminReminderPipelineResult:
    """
    期限リマインダパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "deadlines": [
                {
                    "type": str,
                    "deadline_date": "YYYY-MM-DD",
                    "description": str,
                    "responsible": str,
                },
                ...
            ],
            "reference_date": "YYYY-MM-DD",  # 省略時: today()
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "admin_reminder",
    }

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> AdminReminderPipelineResult:
        return AdminReminderPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: deadline_scanner ─────────────────────────────────────
    s1_start = int(time.time() * 1000)
    deadlines: list[dict] = input_data.get("deadlines", [])
    s1_out = MicroAgentOutput(
        agent_name="deadline_scanner", success=True,
        result={
            "deadlines": deadlines,
            "count": len(deadlines),
            "source": "direct",
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "deadline_scanner", "deadline_scanner", s1_out)
    context["deadlines"] = deadlines

    # ─── Step 2: priority_sorter ──────────────────────────────────────
    s2_start = int(time.time() * 1000)
    ref_date_str = input_data.get("reference_date")
    if ref_date_str:
        try:
            reference_date = date.fromisoformat(ref_date_str)
        except ValueError:
            reference_date = date.today()
    else:
        reference_date = date.today()

    enriched: list[dict] = []
    parse_errors: list[str] = []

    for item in deadlines:
        deadline_str = item.get("deadline_date", "")
        try:
            deadline_date = date.fromisoformat(deadline_str)
            priority = _calc_priority(deadline_date, reference_date)
            delta_days = (deadline_date - reference_date).days
        except (ValueError, TypeError):
            priority = PRIORITY_NORMAL
            delta_days = None
            parse_errors.append(deadline_str)

        enriched.append({
            **item,
            "priority": priority,
            "delta_days": delta_days,
        })

    # 優先度順にソート（overdue → urgent → warning → normal、同一priority内は期限日昇順）
    enriched.sort(key=lambda x: (
        _PRIORITY_ORDER.get(x["priority"], 99),
        x.get("deadline_date", "9999-99-99"),
    ))

    s2_out = MicroAgentOutput(
        agent_name="priority_sorter", success=True,
        result={
            "sorted_deadlines": enriched,
            "reference_date": reference_date.isoformat(),
            "parse_errors": parse_errors,
            "overdue_count": sum(1 for d in enriched if d["priority"] == PRIORITY_OVERDUE),
            "urgent_count": sum(1 for d in enriched if d["priority"] == PRIORITY_URGENT),
            "warning_count": sum(1 for d in enriched if d["priority"] == PRIORITY_WARNING),
            "normal_count": sum(1 for d in enriched if d["priority"] == PRIORITY_NORMAL),
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "priority_sorter", "priority_sorter", s2_out)
    context["sorted_deadlines"] = enriched
    context["reference_date"] = reference_date.isoformat()

    # ─── Step 3: reminder_generator ──────────────────────────────────
    if not enriched:
        # 空リストでも成功返却
        s3_start = int(time.time() * 1000)
        s3_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={
                "content": "期限管理対象がありません。",
                "format": "text",
                "char_count": 12,
                "reminders": [],
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    else:
        s3_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template_name": "summary",
                "data": {
                    "title": "期限リマインダ一覧",
                    "reference_date": reference_date.isoformat(),
                    "deadlines": enriched,
                    "overdue_count": s2_out.result["overdue_count"],
                    "urgent_count": s2_out.result["urgent_count"],
                    "warning_count": s2_out.result["warning_count"],
                },
                "format": "text",
            },
            context=context,
        ))

    _add_step(3, "reminder_generator", "document_generator", s3_out)
    if not s3_out.success:
        return _fail("reminder_generator")

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"admin_reminder_pipeline complete: {len(enriched)} deadlines, "
        f"overdue={s2_out.result['overdue_count']}, urgent={s2_out.result['urgent_count']}, "
        f"{total_duration}ms"
    )

    return AdminReminderPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "reference_date": reference_date.isoformat(),
            "total_count": len(enriched),
            "sorted_deadlines": enriched,
            "overdue_count": s2_out.result["overdue_count"],
            "urgent_count": s2_out.result["urgent_count"],
            "warning_count": s2_out.result["warning_count"],
            "normal_count": s2_out.result["normal_count"],
            "reminder_text": s3_out.result.get("content", ""),
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        reminders=enriched,
    )

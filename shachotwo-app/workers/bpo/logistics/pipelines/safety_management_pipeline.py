"""物流・運送業 安全管理パイプライン

Steps:
  Step 1: extractor           事故・ヒヤリハットデータ構造化
  Step 2: accident_classifier 事故分類（第一当事者/第二当事者・重傷/軽傷/物損）
  Step 3: rule_matcher        Gマーク認定基準チェック（安全性評価基準照合）
  Step 4: training_planner    安全教育計画生成（法定教育時間・ドライバー別）
  Step 5: report_generator    事故報告書・安全教育記録・Gマーク申請書類生成
  Step 6: validator           法定記録確認（事故記録3年保存・教育記録保存義務）
  Step 7: saas_writer         execution_logs保存 + 安全アラート通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# Gマーク認定基準（安全性優良事業所認定）
GMARK_MIN_SCORE = 80              # 最低得点（100点満点）
GMARK_RENEWAL_YEARS = 2           # 更新間隔（年）

# 法定安全教育時間（時間/年）
MIN_SAFETY_TRAINING_HOURS_PER_YEAR = 12  # 初任運転者は35時間

# 事故記録保存年数
ACCIDENT_RETENTION_YEARS = 3

# 事故重大度分類
ACCIDENT_SEVERITY = {
    "fatal": "死亡事故",
    "serious": "重傷事故",
    "minor": "軽傷事故",
    "property": "物損事故",
    "near_miss": "ヒヤリハット",
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
class SafetyManagementResult:
    """安全管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 安全管理パイプライン",
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


async def run_safety_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> SafetyManagementResult:
    """
    安全管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "report_period": str,           # YYYY-MM（月次）または YYYY（年次）
            "accidents": list[{
                "accident_id": str,
                "accident_date": str,       # YYYY-MM-DD
                "driver_id": str,
                "driver_name": str,
                "vehicle_no": str,
                "severity": str,            # fatal/serious/minor/property/near_miss
                "description": str,
                "location": str,
                "countermeasure": str,
            }],
            "drivers": list[{
                "driver_id": str,
                "driver_name": str,
                "hire_date": str,           # YYYY-MM-DD
                "training_hours_ytd": float,  # 年累計教育時間
                "is_new_driver": bool,      # 初任運転者フラグ
            }],
            "gmark_status": {
                "is_certified": bool,
                "certification_expiry": str,  # YYYY-MM-DD
                "last_score": float,
            },
        }

    Returns:
        SafetyManagementResult
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

    def _fail(step_name: str) -> SafetyManagementResult:
        return SafetyManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    accidents = input_data.get("accidents", [])
    drivers = input_data.get("drivers", [])
    gmark_status = input_data.get("gmark_status", {})

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "report_period": "string",
                "accidents": "list",
                "drivers": "list",
                "gmark_status": "object",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: accident_classifier（事故分類）─────────────────────────
    s2_start = int(time.time() * 1000)
    severity_summary: dict[str, int] = {k: 0 for k in ACCIDENT_SEVERITY}
    for accident in accidents:
        sev = accident.get("severity", "near_miss")
        severity_summary[sev] = severity_summary.get(sev, 0) + 1

    s2_out = MicroAgentOutput(
        agent_name="accident_classifier",
        success=True,
        result={
            "total_accidents": len(accidents),
            "severity_summary": severity_summary,
            "fatal_count": severity_summary.get("fatal", 0),
            "serious_count": severity_summary.get("serious", 0),
            "minor_count": severity_summary.get("minor", 0),
            "property_count": severity_summary.get("property", 0),
            "near_miss_count": severity_summary.get("near_miss", 0),
            "classified_accidents": [
                {**a, "severity_label": ACCIDENT_SEVERITY.get(a.get("severity", ""), "不明")}
                for a in accidents
            ],
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "accident_classifier", "accident_classifier", s2_out)
    context["accident_summary"] = s2_out.result

    # ─── Step 3: rule_matcher（Gマーク認定基準チェック）────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "gmark_certification",
            "items": [
                {
                    "accident_count": len(accidents),
                    "fatal_count": severity_summary.get("fatal", 0),
                    "driver_count": len(drivers),
                    "gmark_status": gmark_status,
                    "min_score": GMARK_MIN_SCORE,
                }
            ],
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    context["gmark_check"] = s3_out.result

    # ─── Step 4: training_planner（安全教育計画生成）────────────────────
    s4_start = int(time.time() * 1000)
    training_plans: list[dict[str, Any]] = []
    for driver in drivers:
        ytd_hours = driver.get("training_hours_ytd", 0.0)
        is_new = driver.get("is_new_driver", False)
        required_hours = 35.0 if is_new else float(MIN_SAFETY_TRAINING_HOURS_PER_YEAR)
        remaining_hours = max(0.0, required_hours - ytd_hours)
        training_plans.append({
            "driver_id": driver.get("driver_id", ""),
            "driver_name": driver.get("driver_name", ""),
            "is_new_driver": is_new,
            "required_hours": required_hours,
            "completed_hours": ytd_hours,
            "remaining_hours": remaining_hours,
            "on_track": remaining_hours == 0.0,
        })

    s4_out = MicroAgentOutput(
        agent_name="training_planner",
        success=True,
        result={
            "training_plans": training_plans,
            "insufficient_training_count": sum(
                1 for p in training_plans if not p["on_track"]
            ),
        },
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "training_planner", "training_planner", s4_out)
    context["training_plans"] = s4_out.result

    # ─── Step 5: report_generator（各種書類生成）────────────────────────
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "安全管理報告書",
            "variables": {
                "report_period": input_data.get("report_period", ""),
                "accident_summary": s2_out.result,
                "gmark_check": s3_out.result,
                "training_plans": training_plans,
                "gmark_status": gmark_status,
            },
        },
        context=context,
    ))
    _add_step(5, "report_generator", "document_generator", s5_out)
    context["generated_doc"] = s5_out.result

    # ─── Step 6: validator（法定記録確認）──────────────────────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "accidents": accidents,
                "training_plans": training_plans,
                "report_period": input_data.get("report_period", ""),
            },
            "required_fields": ["accidents", "training_plans", "report_period"],
            "retention_years": ACCIDENT_RETENTION_YEARS,
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", s6_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    fatal_count = severity_summary.get("fatal", 0)
    serious_count = severity_summary.get("serious", 0)
    logger.info(
        f"safety_management_pipeline: company_id={company_id}, "
        f"accidents={len(accidents)}, fatal={fatal_count}, serious={serious_count}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 重大事故発生時はSlack緊急通知
            "fatal_accident_count": fatal_count,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "accident_summary": s2_out.result,
        "gmark_check": s3_out.result,
        "training_plans": s4_out.result,
        "generated_doc": s5_out.result,
        "retention_check": s6_out.result,
    }

    return SafetyManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

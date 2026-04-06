"""物流・運送業 運行管理パイプライン

Steps:
  Step 1: extractor           運行指示データ構造化（荷主×届先×日時×ドライバー）
  Step 2: rule_matcher        改善基準告示チェック（拘束時間・休息期間）
  Step 3: generator           運行指示書PDF生成
  Step 4: rollcall_processor  点呼記録処理（出発前/帰庫後・アルコールチェック値）
  Step 5: log_extractor       運転日報データ抽出（走行距離・実働時間・特記事項）
  Step 6: compliance_checker  法定記録保存チェック（3年保存義務）
  Step 7: saas_writer         execution_logs保存 + Slack通知
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

# 点呼記録：アルコール検知器の閾値（mg/L）
ALCOHOL_LIMIT = 0.15  # 道路交通法施行令

# 拘束時間上限（時間）
MAX_BINDING_HOURS_DAY = 13   # 原則
MAX_BINDING_HOURS_EXTENDED = 15  # 例外（週2回まで）

# 法定保存年数
LEGAL_RETENTION_YEARS = 3


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
class OperationManagementResult:
    """運行管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 運行管理パイプライン",
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


async def run_operation_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> OperationManagementResult:
    """
    運行管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "operation_date": str,               # YYYY-MM-DD
            "driver_id": str,
            "driver_name": str,
            "vehicle_id": str,
            "vehicle_type": str,
            "departure_time": str,               # HH:MM
            "return_time": str,                  # HH:MM
            "destinations": list[{
                "shipper": str,
                "address": str,
                "arrival_time": str,
                "departure_time": str,
                "cargo": str,
                "weight_kg": float,
            }],
            "rollcall": {
                "departure_alcohol": float,      # mg/L
                "return_alcohol": float,         # mg/L
                "health_check": bool,
            },
            "daily_log": {
                "total_distance_km": float,
                "actual_working_hours": float,
                "fuel_consumed_l": float,
                "remarks": str,
            },
        }

    Returns:
        OperationManagementResult
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

    def _fail(step_name: str) -> OperationManagementResult:
        return OperationManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "operation_date": "string",
                "driver_name": "string",
                "vehicle_type": "string",
                "departure_time": "string",
                "return_time": "string",
                "destinations": "list",
                "rollcall": "object",
                "daily_log": "object",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: rule_matcher（改善基準告示チェック）────────────────────
    departure_time = input_data.get("departure_time", "08:00")
    return_time = input_data.get("return_time", "18:00")
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "labor_standard_transport",
            "items": [
                {
                    "driver_id": input_data.get("driver_id", ""),
                    "departure_time": departure_time,
                    "return_time": return_time,
                    "max_binding_hours": MAX_BINDING_HOURS_DAY,
                    "max_binding_hours_extended": MAX_BINDING_HOURS_EXTENDED,
                }
            ],
        },
        context=context,
    ))
    _add_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("rule_matcher")
    context["compliance_result"] = s2_out.result

    # ─── Step 3: generator（運行指示書生成）────────────────────────────
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "運行指示書",
            "variables": {
                "operation_date": input_data.get("operation_date", ""),
                "driver_name": input_data.get("driver_name", ""),
                "vehicle_id": input_data.get("vehicle_id", ""),
                "vehicle_type": input_data.get("vehicle_type", ""),
                "departure_time": departure_time,
                "return_time": return_time,
                "destinations": input_data.get("destinations", []),
            },
        },
        context=context,
    ))
    _add_step(3, "generator", "document_generator", s3_out)
    context["operation_instruction_doc"] = s3_out.result

    # ─── Step 4: rollcall_processor（点呼記録処理）──────────────────────
    s4_start = int(time.time() * 1000)
    rollcall = input_data.get("rollcall", {})
    departure_alcohol = rollcall.get("departure_alcohol", 0.0)
    return_alcohol = rollcall.get("return_alcohol", 0.0)
    health_check = rollcall.get("health_check", True)

    rollcall_violations: list[str] = []
    if departure_alcohol > ALCOHOL_LIMIT:
        rollcall_violations.append(
            f"出発前アルコール検知: {departure_alcohol}mg/L（上限{ALCOHOL_LIMIT}mg/L超過）"
        )
    if return_alcohol > ALCOHOL_LIMIT:
        rollcall_violations.append(
            f"帰庫後アルコール検知: {return_alcohol}mg/L（上限{ALCOHOL_LIMIT}mg/L超過）"
        )
    if not health_check:
        rollcall_violations.append("健康状態確認が未完了")

    s4_out = MicroAgentOutput(
        agent_name="rollcall_processor",
        success=True,
        result={
            "departure_alcohol": departure_alcohol,
            "return_alcohol": return_alcohol,
            "health_check": health_check,
            "violations": rollcall_violations,
            "passed": len(rollcall_violations) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "rollcall_processor", "rollcall_processor", s4_out)
    context["rollcall_result"] = s4_out.result

    # ─── Step 5: log_extractor（運転日報データ抽出）──────────────────────
    s5_start = int(time.time() * 1000)
    daily_log = input_data.get("daily_log", {})
    # TODO: OCRで手書き日報を読み取る場合はrun_document_ocrを使用
    s5_out = MicroAgentOutput(
        agent_name="log_extractor",
        success=True,
        result={
            "total_distance_km": daily_log.get("total_distance_km", 0.0),
            "actual_working_hours": daily_log.get("actual_working_hours", 0.0),
            "fuel_consumed_l": daily_log.get("fuel_consumed_l", 0.0),
            "remarks": daily_log.get("remarks", ""),
            "destinations_count": len(input_data.get("destinations", [])),
        },
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "log_extractor", "log_extractor", s5_out)
    context["daily_log_result"] = s5_out.result

    # ─── Step 6: compliance_checker（法定記録保存チェック）───────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "operation_date": input_data.get("operation_date", ""),
                "driver_name": input_data.get("driver_name", ""),
                "rollcall": rollcall,
                "daily_log": daily_log,
                "destinations": input_data.get("destinations", []),
            },
            "required_fields": [
                "operation_date", "driver_name", "rollcall", "daily_log"
            ],
            "retention_years": LEGAL_RETENTION_YEARS,
        },
        context=context,
    ))
    _add_step(6, "compliance_checker", "output_validator", s6_out)
    context["retention_check"] = s6_out.result

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    logger.info(
        f"operation_management_pipeline: company_id={company_id}, "
        f"driver={input_data.get('driver_name', '')}, "
        f"date={input_data.get('operation_date', '')}, "
        f"rollcall_violations={len(rollcall_violations)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: Slack通知実装
            "rollcall_violation_count": len(rollcall_violations),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "operation_instruction_doc": s3_out.result,
        "rollcall_result": s4_out.result,
        "daily_log_result": s5_out.result,
        "compliance_result": s2_out.result,
        "retention_check": s6_out.result,
        "rollcall_violations": rollcall_violations,
    }

    return OperationManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

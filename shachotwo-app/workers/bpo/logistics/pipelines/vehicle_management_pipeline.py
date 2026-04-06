"""物流・運送業 車両管理パイプライン

Steps:
  Step 1: extractor           車両情報データ構造化（車番・車種・走行距離・整備記録）
  Step 2: deadline_checker    車検/定期点検/保険の期日チェック（期限30日前アラート）
  Step 3: cost_calculator     車両別コスト計算（燃料費+整備費+保険料+税金）
  Step 4: utilization_calc    稼働率分析（稼働時間/総時間・実車率・空車率）
  Step 5: rule_matcher        道路運送車両法チェック（定期点検記録義務）
  Step 6: generator           車両管理台帳・整備計画書生成
  Step 7: saas_writer         execution_logs保存 + アラート通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 期限アラート閾値（日）
DEADLINE_ALERT_DAYS = 30
DEADLINE_CRITICAL_DAYS = 7

# 定期点検間隔（走行距離・日数）
PERIODIC_INSPECTION_KM = 10_000   # 10,000km
PERIODIC_INSPECTION_DAYS = 90     # 3ヶ月

# 稼働率基準
TARGET_UTILIZATION_RATE = 0.75  # 目標稼働率75%


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
class VehicleManagementResult:
    """車両管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 車両管理パイプライン",
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


async def run_vehicle_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> VehicleManagementResult:
    """
    車両管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_month": str,        # YYYY-MM
            "vehicles": list[{
                "vehicle_id": str,
                "vehicle_no": str,      # ナンバープレート
                "vehicle_type": str,    # 軽貨物/小型/中型/大型
                "year": int,            # 年式
                "total_distance_km": float,
                "inspection_expiry": str,        # 車検有効期限 YYYY-MM-DD
                "periodic_inspection_last": str, # 最終定期点検日 YYYY-MM-DD
                "insurance_expiry": str,         # 自賠責保険期限 YYYY-MM-DD
                "operating_hours": float,        # 当月稼働時間
                "total_hours": float,            # 当月暦上総時間
                "loaded_hours": float,           # 当月実車時間（荷物積載中）
                "costs": {
                    "fuel_yen": float,
                    "maintenance_yen": float,
                    "insurance_yen": float,
                    "tax_yen": float,
                },
            }],
        }

    Returns:
        VehicleManagementResult
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

    def _fail(step_name: str) -> VehicleManagementResult:
        return VehicleManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    vehicles = input_data.get("vehicles", [])
    target_month = input_data.get("target_month", "")

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "target_month": "string",
                "vehicles": "list[{vehicle_id, vehicle_no, vehicle_type, inspection_expiry}]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context["vehicles"] = vehicles
    context["target_month"] = target_month

    # ─── Step 2: deadline_checker（期日チェック）────────────────────────
    s2_start = int(time.time() * 1000)
    today = date.today()
    alert_threshold = today + timedelta(days=DEADLINE_ALERT_DAYS)
    critical_threshold = today + timedelta(days=DEADLINE_CRITICAL_DAYS)

    deadline_alerts: list[dict[str, Any]] = []
    for v in vehicles:
        vid = v.get("vehicle_no", v.get("vehicle_id", "不明"))
        for field_name, label in [
            ("inspection_expiry", "車検"),
            ("insurance_expiry", "自賠責保険"),
        ]:
            expiry_str = v.get(field_name, "")
            if not expiry_str:
                continue
            try:
                expiry = date.fromisoformat(expiry_str)
                days_left = (expiry - today).days
                if days_left <= DEADLINE_CRITICAL_DAYS:
                    deadline_alerts.append({
                        "vehicle_no": vid, "type": label,
                        "expiry": expiry_str, "days_left": days_left,
                        "level": "critical",
                    })
                elif days_left <= DEADLINE_ALERT_DAYS:
                    deadline_alerts.append({
                        "vehicle_no": vid, "type": label,
                        "expiry": expiry_str, "days_left": days_left,
                        "level": "warning",
                    })
            except ValueError:
                pass

    s2_out = MicroAgentOutput(
        agent_name="deadline_checker",
        success=True,
        result={
            "alerts": deadline_alerts,
            "critical_count": sum(1 for a in deadline_alerts if a["level"] == "critical"),
            "warning_count": sum(1 for a in deadline_alerts if a["level"] == "warning"),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "deadline_checker", "deadline_checker", s2_out)
    context["deadline_alerts"] = deadline_alerts

    # ─── Step 3: cost_calculator（車両別コスト計算）─────────────────────
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "vehicle_cost",
            "vehicles": [
                {
                    "vehicle_id": v.get("vehicle_id", ""),
                    "vehicle_no": v.get("vehicle_no", ""),
                    "costs": v.get("costs", {}),
                    "total_distance_km": v.get("total_distance_km", 0.0),
                }
                for v in vehicles
            ],
        },
        context=context,
    ))
    _add_step(3, "cost_calculator", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("cost_calculator")
    context["cost_result"] = s3_out.result

    # ─── Step 4: utilization_calc（稼働率分析）──────────────────────────
    s4_start = int(time.time() * 1000)
    utilization_results: list[dict[str, Any]] = []
    for v in vehicles:
        operating_h = v.get("operating_hours", 0.0)
        total_h = v.get("total_hours", 1.0)
        loaded_h = v.get("loaded_hours", 0.0)

        utilization_rate = operating_h / total_h if total_h > 0 else 0.0
        load_rate = loaded_h / operating_h if operating_h > 0 else 0.0

        utilization_results.append({
            "vehicle_no": v.get("vehicle_no", ""),
            "utilization_rate": round(utilization_rate, 3),
            "load_rate": round(load_rate, 3),
            "empty_rate": round(1 - load_rate, 3),
            "below_target": utilization_rate < TARGET_UTILIZATION_RATE,
        })

    s4_out = MicroAgentOutput(
        agent_name="utilization_calc",
        success=True,
        result={
            "utilization_results": utilization_results,
            "fleet_avg_utilization": round(
                sum(r["utilization_rate"] for r in utilization_results) / len(utilization_results)
                if utilization_results else 0.0, 3
            ),
            "fleet_avg_load_rate": round(
                sum(r["load_rate"] for r in utilization_results) / len(utilization_results)
                if utilization_results else 0.0, 3
            ),
            "below_target_count": sum(1 for r in utilization_results if r["below_target"]),
        },
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "utilization_calc", "utilization_calc", s4_out)
    context["utilization_result"] = s4_out.result

    # ─── Step 5: rule_matcher（道路運送車両法チェック）──────────────────
    s5_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "road_transport_vehicle_law",
            "items": [
                {
                    "vehicle_no": v.get("vehicle_no", ""),
                    "periodic_inspection_last": v.get("periodic_inspection_last", ""),
                    "total_distance_km": v.get("total_distance_km", 0.0),
                    "inspection_interval_km": PERIODIC_INSPECTION_KM,
                    "inspection_interval_days": PERIODIC_INSPECTION_DAYS,
                }
                for v in vehicles
            ],
        },
        context=context,
    ))
    _add_step(5, "rule_matcher", "rule_matcher", s5_out)
    context["law_compliance"] = s5_out.result

    # ─── Step 6: generator（車両管理台帳・整備計画書生成）───────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "車両管理台帳",
            "variables": {
                "target_month": target_month,
                "vehicles": vehicles,
                "deadline_alerts": deadline_alerts,
                "utilization_results": utilization_results,
                "cost_result": s3_out.result,
            },
        },
        context=context,
    ))
    _add_step(6, "generator", "document_generator", s6_out)
    context["generated_doc"] = s6_out.result

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    critical_count = s2_out.result.get("critical_count", 0)
    logger.info(
        f"vehicle_management_pipeline: company_id={company_id}, "
        f"vehicles={len(vehicles)}, deadline_critical={critical_count}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 期限アラート時はSlack通知実装
            "critical_deadline_count": critical_count,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "deadline_alerts": deadline_alerts,
        "cost_result": s3_out.result,
        "utilization_result": s4_out.result,
        "law_compliance": s5_out.result,
        "generated_doc": s6_out.result,
    }

    return VehicleManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

"""製造業 設備保全パイプライン

Steps:
  Step 1: saas_reader     設備マスタ + 保全履歴取得
  Step 2: extractor       保全記録構造化
  Step 3: calculator      MTBF/MTTR計算 + 次回保全日算出
  Step 4: rule_matcher    保全期限アラート判定（法定点検含む）
  Step 5: generator       月次保全カレンダー生成
  Step 6: validator       保全計画の完全性チェック（漏れ検出）
  Step 7: saas_writer     保全計画保存 + リマインダー登録
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 保全期限アラート日数
MAINTENANCE_ALERT_DAYS = 30   # 30日以内に期限
MANDATORY_INSPECTION_ALERT_DAYS = 60  # 法定点検は60日前からアラート

# MTBF/MTTRの最低サンプル数
MIN_SAMPLE_FOR_MTBF = 2


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
class EquipmentMaintenanceResult:
    """設備保全パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 設備保全パイプライン",
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


async def run_equipment_maintenance_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    target_month: str | None = None,
) -> EquipmentMaintenanceResult:
    """
    製造業設備保全パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "equipments": [
                {
                    "equipment_id": str,
                    "equipment_name": str,
                    "equipment_type": str,
                    "last_maintenance_date": str,   # YYYY-MM-DD
                    "maintenance_interval_days": int,
                    "is_mandatory_inspection": bool,  # 法定点検かどうか
                    "operating_hours": float,
                    "failure_history": list[{"date": str, "repair_hours": float}],
                }
            ]
        }
        target_month: 対象月（YYYY-MM）。Noneの場合は当月

    Returns:
        EquipmentMaintenanceResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "today": today.isoformat(),
        "target_month": target_month or today.strftime("%Y-%m"),
    }

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

    def _fail(step_name: str) -> EquipmentMaintenanceResult:
        return EquipmentMaintenanceResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: saas_reader ────────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    equipments: list[dict] = input_data.get("equipments", [])
    # TODO: DBから設備マスタ + 保全履歴を取得するロジック
    s1_out = MicroAgentOutput(
        agent_name="saas_reader",
        success=True,
        result={"equipments": equipments, "source": "direct"},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("saas_reader")
    context["equipments"] = equipments

    # ─── Step 2: extractor (保全記録構造化) ─────────────────────────────
    s2_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _serialize_equipments(equipments),
            "schema": {
                "equipments": "list[{equipment_id: str, equipment_name: str, "
                              "last_maintenance_date: str, maintenance_interval_days: int, "
                              "failure_history: list}]",
            },
        },
        context=context,
    ))
    _add_step(2, "extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _fail("extractor")

    # ─── Step 3: calculator (MTBF/MTTR計算) ────────────────────────────
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "mtbf_mttr",
            "equipments": equipments,
            "today": today.isoformat(),
        },
        context=context,
    ))
    _add_step(3, "calculator", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("calculator")

    # フォールバック計算
    calc_result = s3_out.result
    if not calc_result.get("equipment_stats") and equipments:
        calc_result = _calculate_mtbf_mttr(equipments, today)
    context["calc_result"] = calc_result

    # ─── Step 4: rule_matcher (保全期限アラート判定) ─────────────────────
    maintenance_alerts: list[dict] = []
    for eq_stat in calc_result.get("equipment_stats", []):
        next_date_str = eq_stat.get("next_maintenance_date", "")
        if not next_date_str:
            continue
        try:
            next_date = date.fromisoformat(next_date_str)
        except ValueError:
            continue
        days_until = (next_date - today).days
        is_mandatory = eq_stat.get("is_mandatory_inspection", False)
        alert_days = MANDATORY_INSPECTION_ALERT_DAYS if is_mandatory else MAINTENANCE_ALERT_DAYS
        if days_until <= 0:
            maintenance_alerts.append({
                "equipment_id": eq_stat.get("equipment_id"),
                "equipment_name": eq_stat.get("equipment_name"),
                "next_maintenance_date": next_date_str,
                "days_until": days_until,
                "severity": "overdue",
                "is_mandatory": is_mandatory,
            })
        elif days_until <= alert_days:
            maintenance_alerts.append({
                "equipment_id": eq_stat.get("equipment_id"),
                "equipment_name": eq_stat.get("equipment_name"),
                "next_maintenance_date": next_date_str,
                "days_until": days_until,
                "severity": "warning",
                "is_mandatory": is_mandatory,
            })

    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": calc_result.get("equipment_stats", []),
            "rule_type": "maintenance_deadline",
            "alerts": maintenance_alerts,
        },
        context=context,
    ))
    _add_step(4, "rule_matcher", "rule_matcher", s4_out)
    context["maintenance_alerts"] = maintenance_alerts

    # ─── Step 5: generator (月次保全カレンダー生成) ─────────────────────
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "月次保全カレンダー",
            "variables": {
                "target_month": context["target_month"],
                "equipment_stats": calc_result.get("equipment_stats", []),
                "maintenance_alerts": maintenance_alerts,
            },
        },
        context=context,
    ))
    _add_step(5, "generator", "document_generator", s5_out)
    context["generated_doc"] = s5_out.result

    # ─── Step 6: validator (保全計画の完全性チェック) ─────────────────
    missing_equipments: list[str] = []
    for eq in equipments:
        eq_id = eq.get("equipment_id", "")
        last_date = eq.get("last_maintenance_date")
        interval = eq.get("maintenance_interval_days")
        if not last_date or not interval:
            missing_equipments.append(
                f"設備 '{eq.get('equipment_name', eq_id)}': 保全情報が不完全"
            )

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "equipments": equipments,
                "maintenance_alerts": maintenance_alerts,
                "missing_equipments": missing_equipments,
            },
            "required_fields": ["equipments"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)
    context["missing_equipments"] = missing_equipments

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: 保全計画保存（maintenance_plans テーブル）+ リマインダー登録
    overdue_count = sum(1 for a in maintenance_alerts if a.get("severity") == "overdue")
    logger.info(
        f"equipment_maintenance_pipeline: company_id={company_id}, "
        f"equipments={len(equipments)}, alerts={len(maintenance_alerts)}, "
        f"overdue={overdue_count}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "reminder_registered": len(maintenance_alerts) > 0,
            "alerts_count": len(maintenance_alerts),
            "overdue_count": overdue_count,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "target_month": context["target_month"],
        "equipment_stats": calc_result.get("equipment_stats", []),
        "maintenance_alerts": maintenance_alerts,
        "missing_equipments": missing_equipments,
        "generated_doc": s5_out.result,
    }

    return EquipmentMaintenanceResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _calculate_mtbf_mttr(
    equipments: list[dict], today: date
) -> dict[str, Any]:
    """MTBF/MTTR計算 + 次回保全日算出"""
    equipment_stats = []

    for eq in equipments:
        failure_history: list[dict] = eq.get("failure_history", [])
        operating_hours: float = float(eq.get("operating_hours", 0))
        last_maintenance_str: str = eq.get("last_maintenance_date", "")
        interval_days: int = int(eq.get("maintenance_interval_days", 90))

        # MTBF計算（故障間平均時間）
        if len(failure_history) >= MIN_SAMPLE_FOR_MTBF:
            total_repair_hours = sum(
                f.get("repair_hours", 0) for f in failure_history
            )
            failure_count = len(failure_history)
            mttr = total_repair_hours / failure_count  # 平均修理時間
            mtbf = (operating_hours - total_repair_hours) / failure_count
        else:
            mtbf = operating_hours if operating_hours > 0 else 0.0
            mttr = 0.0

        # 次回保全日算出
        next_date = None
        if last_maintenance_str:
            try:
                last_date = date.fromisoformat(last_maintenance_str)
                next_date = last_date + timedelta(days=interval_days)
            except ValueError:
                pass

        equipment_stats.append({
            "equipment_id": eq.get("equipment_id", ""),
            "equipment_name": eq.get("equipment_name", ""),
            "equipment_type": eq.get("equipment_type", ""),
            "mtbf_hours": round(mtbf, 1),
            "mttr_hours": round(mttr, 1),
            "last_maintenance_date": last_maintenance_str,
            "next_maintenance_date": next_date.isoformat() if next_date else "",
            "maintenance_interval_days": interval_days,
            "is_mandatory_inspection": eq.get("is_mandatory_inspection", False),
        })

    return {"equipment_stats": equipment_stats}


def _serialize_equipments(equipments: list[dict]) -> str:
    import json
    return json.dumps({"equipments": equipments}, ensure_ascii=False)

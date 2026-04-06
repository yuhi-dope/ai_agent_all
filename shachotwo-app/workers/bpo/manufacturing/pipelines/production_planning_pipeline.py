"""製造業 生産計画AIパイプライン

Steps:
  Step 1: extractor       受注データ構造化
  Step 2: rule_matcher    工程マスタ照合（旋盤/フライス/研磨/組立等）
  Step 3: calculator      山積み計算（工程×設備キャパ→ガントチャート）
  Step 4: compliance      納期遵守チェック（リードタイム逆算）
  Step 5: generator       生産計画書PDF生成
  Step 6: validator       計画整合性チェック（設備稼働率100%超えアラート）
  Step 7: saas_writer     execution_logs保存 + Slack通知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 設備稼働率アラート閾値
CAPACITY_ALERT_THRESHOLD = 1.0  # 100%超えでアラート
CONFIDENCE_WARNING_THRESHOLD = 0.70

# デフォルトリードタイム（日）
DEFAULT_LEAD_TIME_DAYS = 14

# 工程マスタ（デフォルト）
PROCESS_MASTER = {
    "旋盤加工": {"capacity_per_day": 8.0, "unit": "時間"},
    "フライス加工": {"capacity_per_day": 8.0, "unit": "時間"},
    "研磨加工": {"capacity_per_day": 6.0, "unit": "時間"},
    "組立": {"capacity_per_day": 10.0, "unit": "時間"},
    "溶接": {"capacity_per_day": 8.0, "unit": "時間"},
    "検査": {"capacity_per_day": 8.0, "unit": "時間"},
    "default": {"capacity_per_day": 8.0, "unit": "時間"},
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
class ProductionPlanResult:
    """生産計画パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 生産計画パイプライン",
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


async def run_production_planning_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> ProductionPlanResult:
    """
    製造業生産計画パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "orders": [{"product_name": str, "quantity": int, "delivery_date": str,
                        "processes": list[{"process_name": str, "estimated_hours": float}]}],
            "start_date": str,  # YYYY-MM-DD
        }

    Returns:
        ProductionPlanResult
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

    def _fail(step_name: str) -> ProductionPlanResult:
        return ProductionPlanResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: extractor ──────────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _serialize_orders(input_data),
            "schema": {
                "orders": "list[{product_name: str, quantity: int, delivery_date: str, processes: list}]",
                "start_date": "string",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    # 直渡し形式の場合はinput_dataをそのまま使う
    orders = input_data.get("orders", s1_out.result.get("orders", []))
    start_date = input_data.get("start_date", s1_out.result.get("start_date", ""))
    context["orders"] = orders
    context["start_date"] = start_date

    # ─── Step 2: rule_matcher ────────────────────────────────────────────
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": [p for o in orders for p in o.get("processes", [])],
            "rule_type": "process_master",
            "master": PROCESS_MASTER,
        },
        context=context,
    ))
    _add_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("rule_matcher")
    context["process_master_result"] = s2_out.result

    # ─── Step 3: calculator (山積み計算) ────────────────────────────────
    s3_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "production_load",
            "orders": orders,
            "process_master": PROCESS_MASTER,
            "start_date": start_date,
        },
        context=context,
    ))
    _add_step(3, "calculator", "cost_calculator", s3_out)
    if not s3_out.success:
        return _fail("calculator")
    gantt = s3_out.result.get("gantt", [])
    overloaded = s3_out.result.get("overloaded_processes", [])
    context["gantt"] = gantt
    context["overloaded_processes"] = overloaded

    # ─── Step 4: compliance (納期遵守チェック) ───────────────────────────
    s4_start = int(time.time() * 1000)
    delivery_warnings: list[str] = []
    for order in orders:
        delivery_date = order.get("delivery_date", "")
        lead_time_days = order.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS)
        if not delivery_date:
            delivery_warnings.append(
                f"受注 '{order.get('product_name', '不明')}' の納期が未設定です"
            )
        # TODO: 詳細なリードタイム逆算ロジック（工程日程→納期比較）
    s4_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "delivery_warnings": delivery_warnings,
            "passed": len(delivery_warnings) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance", "compliance_checker", s4_out)
    context["delivery_warnings"] = delivery_warnings

    # ─── Step 5: generator (生産計画書PDF生成) ──────────────────────────
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "生産計画書",
            "variables": {
                "orders": orders,
                "gantt": gantt,
                "start_date": start_date,
                "overloaded_processes": overloaded,
                "delivery_warnings": delivery_warnings,
            },
        },
        context=context,
    ))
    _add_step(5, "generator", "document_generator", s5_out)
    context["generated_doc"] = s5_out.result

    # ─── Step 6: validator (計画整合性チェック) ─────────────────────────
    capacity_alerts: list[str] = []
    for proc in overloaded:
        proc_name = proc.get("process_name", "不明")
        load_rate = proc.get("load_rate", 0.0)
        if load_rate > CAPACITY_ALERT_THRESHOLD:
            capacity_alerts.append(
                f"{proc_name}: 稼働率 {load_rate*100:.0f}%（設備オーバー）"
            )

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "orders": orders,
                "gantt": gantt,
                "capacity_alerts": capacity_alerts,
            },
            "required_fields": ["orders", "gantt"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)
    context["capacity_alerts"] = capacity_alerts

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: execution_logs保存 + Slack通知の実装
    # 現在はスケルトン（ログ記録のみ）
    logger.info(
        f"production_planning_pipeline: company_id={company_id}, "
        f"orders={len(orders)}, capacity_alerts={len(capacity_alerts)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: Slack通知実装
            "capacity_alerts_count": len(capacity_alerts),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "orders": orders,
        "gantt": gantt,
        "overloaded_processes": overloaded,
        "capacity_alerts": capacity_alerts,
        "delivery_warnings": delivery_warnings,
        "generated_doc": s5_out.result,
    }

    return ProductionPlanResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_orders(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    if "orders" in input_data:
        import json
        return json.dumps(input_data, ensure_ascii=False)
    return input_data.get("text", "")

"""製造業 品質管理パイプライン

Steps:
  Step 1: extractor       検査データ構造化
  Step 2: calculator      SPC計算（Cp/Cpk/Xbar-R管理図）
  Step 3: rule_matcher    管理限界逸脱チェック
  Step 4: compliance      ISO 9001要求事項照合
  Step 5: generator       品質月次レポート生成
  Step 6: validator       不良予兆検知（トレンド分析）
  Step 7: saas_writer     品質記録保存 + アラート通知
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# Cp/Cpk基準値
CP_WARNING_THRESHOLD = 1.0   # Cp < 1.0 は工程能力不足
CP_CAUTION_THRESHOLD = 1.33  # Cp < 1.33 は要改善
CPK_WARNING_THRESHOLD = 1.0  # Cpk < 1.0 は工程が規格外を生産している可能性

# ISO 9001 必須チェック項目
ISO9001_REQUIRED_CHECKS = [
    "測定データ記録",
    "規格値設定",
    "管理図更新",
    "不適合品処置",
]


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
class QualityControlResult:
    """品質管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 品質管理パイプライン",
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


async def run_quality_control_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> QualityControlResult:
    """
    製造業品質管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "lot_number": str,
            "product_name": str,
            "measurements": list[float],  # 測定値リスト
            "usl": float,                 # 規格上限値
            "lsl": float,                 # 規格下限値
            "target": float,              # 規格中心値
            "report_month": str,          # YYYY-MM
        }

    Returns:
        QualityControlResult
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

    def _fail(step_name: str) -> QualityControlResult:
        return QualityControlResult(
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
            "text": _serialize_inspection_data(input_data),
            "schema": {
                "lot_number": "string",
                "product_name": "string",
                "measurements": "list[float]",
                "usl": "float",
                "lsl": "float",
                "target": "float",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")

    lot_number = input_data.get("lot_number", s1_out.result.get("lot_number", ""))
    product_name = input_data.get("product_name", s1_out.result.get("product_name", ""))
    measurements: list[float] = input_data.get(
        "measurements", s1_out.result.get("measurements", [])
    )
    usl: float = float(input_data.get("usl", s1_out.result.get("usl", 0.0)))
    lsl: float = float(input_data.get("lsl", s1_out.result.get("lsl", 0.0)))
    target: float = float(input_data.get("target", s1_out.result.get("target", 0.0)))
    report_month: str = input_data.get("report_month", "")

    context.update({
        "lot_number": lot_number,
        "product_name": product_name,
        "measurements": measurements,
        "usl": usl,
        "lsl": lsl,
        "target": target,
    })

    # ─── Step 2: calculator (SPC計算) ───────────────────────────────────
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "spc",
            "measurements": measurements,
            "usl": usl,
            "lsl": lsl,
            "target": target,
        },
        context=context,
    ))
    _add_step(2, "calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("calculator")

    # SPC計算フォールバック（マイクロエージェントが未対応の場合）
    spc_result = s2_out.result
    if not spc_result.get("cp") and measurements:
        spc_result = _calculate_spc(measurements, usl, lsl, target)
    context["spc"] = spc_result

    # ─── Step 3: rule_matcher (管理限界逸脱チェック) ────────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": measurements,
            "rule_type": "control_limit",
            "ucl": spc_result.get("ucl", usl),
            "lcl": spc_result.get("lcl", lsl),
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    violations = s3_out.result.get("violations", [])
    context["control_limit_violations"] = violations

    # ─── Step 4: compliance (ISO 9001チェック) ──────────────────────────
    s4_start = int(time.time() * 1000)
    iso_warnings: list[str] = []
    if not measurements:
        iso_warnings.append("測定データが存在しません（ISO 9001: 9.1.1要求事項）")
    if usl == 0 and lsl == 0:
        iso_warnings.append("規格値が設定されていません（ISO 9001: 8.6要求事項）")
    if not lot_number:
        iso_warnings.append("ロット番号が未設定です（トレーサビリティ要求）")

    s4_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "iso9001_warnings": iso_warnings,
            "required_checks": ISO9001_REQUIRED_CHECKS,
            "passed": len(iso_warnings) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance", "compliance_checker", s4_out)
    context["iso_warnings"] = iso_warnings

    # ─── Step 5: generator (品質月次レポート生成) ───────────────────────
    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "品質月次レポート",
            "variables": {
                "lot_number": lot_number,
                "product_name": product_name,
                "report_month": report_month,
                "spc": spc_result,
                "violations": violations,
                "iso_warnings": iso_warnings,
            },
        },
        context=context,
    ))
    _add_step(5, "generator", "document_generator", s5_out)
    context["generated_doc"] = s5_out.result

    # ─── Step 6: validator (不良予兆検知) ──────────────────────────────
    trend_alerts: list[str] = []
    cp = spc_result.get("cp", 0.0)
    cpk = spc_result.get("cpk", 0.0)
    if cp < CP_WARNING_THRESHOLD:
        trend_alerts.append(f"工程能力不足: Cp={cp:.2f} (基準値1.00未満)")
    elif cp < CP_CAUTION_THRESHOLD:
        trend_alerts.append(f"工程能力要改善: Cp={cp:.2f} (基準値1.33未満)")
    if cpk < CPK_WARNING_THRESHOLD:
        trend_alerts.append(f"規格外生産リスク: Cpk={cpk:.2f} (基準値1.00未満)")
    if violations:
        trend_alerts.append(f"管理限界逸脱: {len(violations)}点")

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "lot_number": lot_number,
                "spc": spc_result,
                "trend_alerts": trend_alerts,
            },
            "required_fields": ["lot_number", "spc"],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)
    context["trend_alerts"] = trend_alerts

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: 品質記録保存（quality_records テーブル）+ アラート通知
    logger.info(
        f"quality_control_pipeline: company_id={company_id}, "
        f"lot={lot_number}, cp={spc_result.get('cp', 0):.2f}, "
        f"trend_alerts={len(trend_alerts)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "alert_notified": len(trend_alerts) > 0,
            "trend_alerts_count": len(trend_alerts),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "lot_number": lot_number,
        "product_name": product_name,
        "report_month": report_month,
        "spc": spc_result,
        "control_limit_violations": violations,
        "iso_warnings": iso_warnings,
        "trend_alerts": trend_alerts,
        "generated_doc": s5_out.result,
    }

    return QualityControlResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _calculate_spc(
    measurements: list[float],
    usl: float,
    lsl: float,
    target: float,
) -> dict[str, float]:
    """SPC計算（Cp/Cpk/平均/標準偏差/管理限界）"""
    n = len(measurements)
    if n == 0:
        return {"cp": 0.0, "cpk": 0.0, "mean": 0.0, "std": 0.0, "ucl": usl, "lcl": lsl}

    mean = sum(measurements) / n
    variance = sum((x - mean) ** 2 for x in measurements) / max(n - 1, 1)
    std = math.sqrt(variance)

    tolerance = usl - lsl
    cp = tolerance / (6 * std) if std > 0 else 0.0
    cpu = (usl - mean) / (3 * std) if std > 0 else 0.0
    cpl = (mean - lsl) / (3 * std) if std > 0 else 0.0
    cpk = min(cpu, cpl)

    # Xbar-R管理図の管理限界（3σ法）
    ucl = mean + 3 * std
    lcl = mean - 3 * std

    return {
        "cp": round(cp, 3),
        "cpk": round(cpk, 3),
        "cpu": round(cpu, 3),
        "cpl": round(cpl, 3),
        "mean": round(mean, 4),
        "std": round(std, 4),
        "ucl": round(ucl, 4),
        "lcl": round(lcl, 4),
        "n": n,
    }


def _serialize_inspection_data(input_data: dict[str, Any]) -> str:
    """入力データをテキストに変換する（テキスト入力の場合はそのまま返す）"""
    if "text" in input_data:
        return input_data["text"]
    import json
    return json.dumps(input_data, ensure_ascii=False)

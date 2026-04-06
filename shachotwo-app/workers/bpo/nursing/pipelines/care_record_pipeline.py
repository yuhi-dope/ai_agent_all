"""介護・福祉業 記録・日誌AIパイプライン

Steps:
  Step 1: extractor          バイタル・介護記録データ構造化
  Step 2: soap_generator     SOAP形式日誌ドラフト生成（主観/客観/アセスメント/プラン）
  Step 3: anomaly_detector   状態変化検知（バイタル異常値・前回比変化）
  Step 4: rule_matcher       介護記録法定要件照合（記録義務項目の充足確認）
  Step 5: compliance_checker プライバシー・個人情報保護チェック
  Step 6: validator          出力バリデーション（記録者氏名・日時・必須項目）
  Step 7: saas_writer        execution_logs保存 + 異常時家族/医療機関へのアラート
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

# バイタルサインの正常範囲
VITAL_NORMAL_RANGES: dict[str, dict[str, float]] = {
    "blood_pressure_systolic": {"min": 90.0, "max": 140.0},
    "blood_pressure_diastolic": {"min": 60.0, "max": 90.0},
    "pulse": {"min": 50.0, "max": 100.0},
    "temperature": {"min": 35.5, "max": 37.5},
    "spo2": {"min": 95.0, "max": 100.0},
}

# 記録に必須の項目
REQUIRED_RECORD_FIELDS = [
    "記録日時",
    "記録者氏名",
    "利用者氏名",
    "サービス内容",
    "利用者の状態",
]


@dataclass
class VitalAlert:
    item: str
    value: float
    normal_min: float
    normal_max: float
    severity: str  # "WARNING" or "CRITICAL"


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
class CareRecordResult:
    """記録・日誌AIパイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 記録・日誌AIパイプライン",
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


async def run_care_record_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> CareRecordResult:
    """
    記録・日誌AIパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "user_id": str,
            "user_name": str,
            "record_date": str,       # YYYY-MM-DD HH:MM
            "recorder_name": str,     # 記録者氏名
            "service_type": str,      # サービス種別
            "vitals": {
                "blood_pressure_systolic": float,    # 収縮期血圧
                "blood_pressure_diastolic": float,   # 拡張期血圧
                "pulse": float,
                "temperature": float,
                "spo2": float,
            },
            "care_notes": str,        # 介護メモ（自由記述）
            "previous_vitals": dict,  # 前回バイタル（任意）
        }

    Returns:
        CareRecordResult
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

    def _fail(step_name: str) -> CareRecordResult:
        return CareRecordResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: extractor ──────────────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": input_data.get("care_notes", ""),
            "schema": {
                "user_id": "string",
                "user_name": "string",
                "record_date": "string",
                "recorder_name": "string",
                "service_type": "string",
                "vitals": "dict",
                "care_notes": "string",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    # 直渡しデータ優先
    vitals = input_data.get("vitals", s1_out.result.get("vitals", {}))
    user_name = input_data.get("user_name", s1_out.result.get("user_name", ""))
    care_notes = input_data.get("care_notes", s1_out.result.get("care_notes", ""))
    context.update({
        "user_id": input_data.get("user_id", ""),
        "user_name": user_name,
        "record_date": input_data.get("record_date", ""),
        "recorder_name": input_data.get("recorder_name", ""),
        "vitals": vitals,
        "care_notes": care_notes,
    })

    # ─── Step 2: soap_generator ─────────────────────────────────────────
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "SOAP形式介護記録",
            "variables": {
                "user_name": user_name,
                "record_date": input_data.get("record_date", ""),
                "recorder_name": input_data.get("recorder_name", ""),
                "service_type": input_data.get("service_type", ""),
                "vitals": vitals,
                "care_notes": care_notes,
            },
        },
        context=context,
    ))
    _add_step(2, "soap_generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("soap_generator")
    soap_record = s2_out.result
    context["soap_record"] = soap_record

    # ─── Step 3: anomaly_detector ───────────────────────────────────────
    s3_start = int(time.time() * 1000)
    vital_alerts: list[dict[str, Any]] = []
    for vital_key, value in vitals.items():
        if vital_key in VITAL_NORMAL_RANGES and value is not None:
            rng = VITAL_NORMAL_RANGES[vital_key]
            if value < rng["min"] or value > rng["max"]:
                severity = "CRITICAL" if (
                    value < rng["min"] * 0.9 or value > rng["max"] * 1.1
                ) else "WARNING"
                vital_alerts.append({
                    "item": vital_key,
                    "value": value,
                    "normal_min": rng["min"],
                    "normal_max": rng["max"],
                    "severity": severity,
                })

    # 前回バイタルとの比較
    change_alerts: list[str] = []
    previous_vitals = input_data.get("previous_vitals", {})
    if previous_vitals:
        for key, curr_val in vitals.items():
            prev_val = previous_vitals.get(key)
            if prev_val and curr_val is not None:
                change_pct = abs(curr_val - prev_val) / (prev_val + 1e-9)
                if change_pct > 0.2:  # 20%以上の変化
                    change_alerts.append(
                        f"{key}: 前回 {prev_val} -> 今回 {curr_val} "
                        f"({change_pct*100:.0f}%変化)"
                    )

    s3_out = MicroAgentOutput(
        agent_name="anomaly_detector",
        success=True,
        result={
            "vital_alerts": vital_alerts,
            "change_alerts": change_alerts,
            "has_critical": any(a["severity"] == "CRITICAL" for a in vital_alerts),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "anomaly_detector", "anomaly_detector", s3_out)
    context["vital_alerts"] = vital_alerts
    context["change_alerts"] = change_alerts

    # ─── Step 4: rule_matcher（記録法定要件照合） ───────────────────────────
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": [context],
            "rule_type": "care_record_requirements",
            "required_fields": REQUIRED_RECORD_FIELDS,
        },
        context=context,
    ))
    _add_step(4, "rule_matcher", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("rule_matcher")
    record_issues = s4_out.result.get("unmatched", [])
    context["record_issues"] = record_issues

    # ─── Step 5: compliance_checker ─────────────────────────────────────
    s5_start = int(time.time() * 1000)
    privacy_warnings: list[str] = []
    # 個人情報保護：記録に不要な第三者情報が含まれていないか確認
    # TODO: PII検出（Phase 2+でNER/LLM導入）
    if "家族" in care_notes and "連絡先" in care_notes:
        privacy_warnings.append(
            "記録に家族の個人情報（連絡先）が含まれている可能性があります。"
            "個人情報保護法に基づき適切に管理してください。"
        )
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "privacy_warnings": privacy_warnings,
            "passed": len(privacy_warnings) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["privacy_warnings"] = privacy_warnings

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": soap_record,
            "required_fields": ["content"],
            "record_issues": record_issues,
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    has_critical = any(a["severity"] == "CRITICAL" for a in vital_alerts)
    # TODO: 異常時は家族・医療機関へのアラート送信
    logger.info(
        f"care_record_pipeline: company_id={company_id}, "
        f"user_name={user_name}, "
        f"vital_alerts={len(vital_alerts)}, "
        f"has_critical={has_critical}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "alert_sent": False,  # TODO: 緊急アラート実装
            "vital_alerts_count": len(vital_alerts),
            "has_critical": has_critical,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "user_name": user_name,
        "soap_record": soap_record,
        "vital_alerts": vital_alerts,
        "change_alerts": change_alerts,
        "record_issues": record_issues,
        "privacy_warnings": privacy_warnings,
        "has_critical": has_critical,
    }

    return CareRecordResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

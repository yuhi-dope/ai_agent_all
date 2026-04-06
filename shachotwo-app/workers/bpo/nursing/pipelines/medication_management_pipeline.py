"""介護・福祉業 服薬管理パイプライン

Steps:
  Step 1: extractor           処方箋・服薬指示データ構造化
  Step 2: schedule_generator  服薬スケジュール生成（朝・昼・夕・就寝前×利用者別）
  Step 3: interaction_checker 薬物相互作用チェック（薬剤DBとの照合）
  Step 4: rule_matcher        医師指示書・介護記録との整合性確認
  Step 5: compliance_checker  服薬介助適法性確認（介護職員が行える範囲の確認）
  Step 6: validator           出力バリデーション（利用者氏名・薬剤名・用量・期間）
  Step 7: saas_writer         execution_logs保存 + 服薬漏れアラート設定
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

# 服薬タイミング
MEDICATION_TIMINGS = ["朝食後", "昼食後", "夕食後", "就寝前", "食前", "頓服"]

# 介護職員が行える服薬介助の範囲（省令第3条関係）
ALLOWED_MEDICATION_ASSISTANCE = [
    "錠剤・カプセルの手渡し",
    "水を用意する",
    "服薬を見守る・確認する",
    "軟膏の塗布（褥瘡等を除く）",
    "点眼薬の点眼",
    "湿布の貼付",
    "座薬の挿入",
    "鼻腔粘膜への薬剤塗布",
]

# 重篤な相互作用のある薬剤ペア例（スケルトン用。実装では薬剤DBを参照）
KNOWN_INTERACTIONS: list[tuple[str, str, str]] = [
    ("ワルファリン", "アスピリン", "出血リスク増大"),
    ("ジゴキシン", "アミオダロン", "ジゴキシン中毒リスク"),
    ("リチウム", "NSAIDs", "リチウム中毒リスク"),
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
class MedicationManagementResult:
    """服薬管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 服薬管理パイプライン",
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


async def run_medication_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> MedicationManagementResult:
    """
    服薬管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "user_id": str,
            "user_name": str,
            "prescription_date": str,  # YYYY-MM-DD
            "doctor_name": str,
            "medications": [
                {
                    "drug_name": str,       # 薬剤名
                    "dosage": str,          # 用量（例: "1錠"）
                    "timing": str,          # 服薬タイミング（例: "朝食後"）
                    "duration_days": int,   # 服薬期間（日数）
                    "notes": str,           # 注意事項
                }
            ],
        }

    Returns:
        MedicationManagementResult
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

    def _fail(step_name: str) -> MedicationManagementResult:
        return MedicationManagementResult(
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
            "text": _serialize_prescription(input_data),
            "schema": {
                "user_id": "string",
                "user_name": "string",
                "prescription_date": "string",
                "doctor_name": "string",
                "medications": "list[{drug_name, dosage, timing, duration_days, notes}]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    medications = input_data.get("medications", s1_out.result.get("medications", []))
    user_name = input_data.get("user_name", s1_out.result.get("user_name", ""))
    context.update({
        "user_id": input_data.get("user_id", ""),
        "user_name": user_name,
        "medications": medications,
        "prescription_date": input_data.get("prescription_date", ""),
    })

    # ─── Step 2: schedule_generator ─────────────────────────────────────
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "服薬管理スケジュール",
            "variables": {
                "user_name": user_name,
                "prescription_date": input_data.get("prescription_date", ""),
                "medications": medications,
                "timings": MEDICATION_TIMINGS,
            },
        },
        context=context,
    ))
    _add_step(2, "schedule_generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("schedule_generator")
    medication_schedule = s2_out.result
    context["medication_schedule"] = medication_schedule

    # ─── Step 3: interaction_checker ────────────────────────────────────
    s3_start = int(time.time() * 1000)
    interaction_warnings: list[str] = []
    drug_names = [m.get("drug_name", "") for m in medications]
    for i, drug_a in enumerate(drug_names):
        for drug_b in drug_names[i + 1:]:
            for known_a, known_b, risk in KNOWN_INTERACTIONS:
                if (known_a in drug_a and known_b in drug_b) or \
                   (known_b in drug_a and known_a in drug_b):
                    interaction_warnings.append(
                        f"相互作用警告: {drug_a} × {drug_b} → {risk}"
                    )
    s3_out = MicroAgentOutput(
        agent_name="interaction_checker",
        success=True,
        result={
            "interaction_warnings": interaction_warnings,
            "has_critical_interaction": len(interaction_warnings) > 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "interaction_checker", "interaction_checker", s3_out)
    context["interaction_warnings"] = interaction_warnings

    # ─── Step 4: rule_matcher（医師指示書との整合性） ────────────────────
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": medications,
            "rule_type": "prescription_compliance",
            "allowed_timings": MEDICATION_TIMINGS,
        },
        context=context,
    ))
    _add_step(4, "rule_matcher", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("rule_matcher")
    prescription_issues = s4_out.result.get("unmatched", [])
    context["prescription_issues"] = prescription_issues

    # ─── Step 5: compliance_checker（服薬介助適法性） ──────────────────
    s5_start = int(time.time() * 1000)
    legal_warnings: list[str] = []
    for med in medications:
        notes = med.get("notes", "")
        drug_name = med.get("drug_name", "")
        # 注射・点滴など医療行為に該当するものをフラグ
        if any(kw in notes or kw in drug_name for kw in ["注射", "点滴", "インスリン", "吸引"]):
            legal_warnings.append(
                f"薬剤「{drug_name}」の投与は医療行為に該当する可能性があります。"
                "看護師または医師が実施してください。"
            )
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "legal_warnings": legal_warnings,
            "passed": len(legal_warnings) == 0,
        },
        confidence=1.0 if len(legal_warnings) == 0 else 0.6,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["legal_warnings"] = legal_warnings

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": medication_schedule,
            "required_fields": ["content"],
            "interaction_warnings": interaction_warnings,
            "legal_warnings": legal_warnings,
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: 服薬漏れアラート設定・execution_logs保存
    logger.info(
        f"medication_management_pipeline: company_id={company_id}, "
        f"user_name={user_name}, "
        f"medications={len(medications)}, "
        f"interaction_warnings={len(interaction_warnings)}, "
        f"legal_warnings={len(legal_warnings)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "reminder_set": False,  # TODO: 服薬リマインダー実装
            "interaction_warnings_count": len(interaction_warnings),
            "legal_warnings_count": len(legal_warnings),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "user_name": user_name,
        "medications": medications,
        "medication_schedule": medication_schedule,
        "interaction_warnings": interaction_warnings,
        "prescription_issues": prescription_issues,
        "legal_warnings": legal_warnings,
    }

    return MedicationManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_prescription(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    import json
    return json.dumps(input_data, ensure_ascii=False)

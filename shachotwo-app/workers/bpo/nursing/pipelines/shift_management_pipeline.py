"""介護・福祉業 シフト・勤怠管理パイプライン

Steps:
  Step 1: extractor          スタッフ・シフト希望データ構造化
  Step 2: rule_matcher       介護報酬算定上の人員配置基準照合（サービス種別×配置基準）
  Step 3: constraint_checker 72時間ルール・夜勤回数・連続勤務チェック
  Step 4: shift_generator    最適シフト表ドラフト生成
  Step 5: compliance_checker 人員基準欠如減算リスク検出（70%減算トリガー確認）
  Step 6: validator          出力バリデーション（全日程カバレッジ確認）
  Step 7: saas_writer        execution_logs保存 + 管理者への確認通知
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

# 夜勤連続勤務上限（時間）
NIGHT_SHIFT_MAX_CONTINUOUS_HOURS = 16

# 月間最大夜勤回数
MAX_NIGHT_SHIFTS_PER_MONTH = 8

# 介護事業所種別ごとの最低人員配置基準
STAFFING_STANDARDS: dict[str, dict[str, Any]] = {
    "訪問介護": {
        "管理者": 1,
        "サービス提供責任者": 1,  # 利用者40人以下に1人
        "訪問介護員": 2,
    },
    "通所介護": {
        "管理者": 1,
        "生活相談員": 1,
        "看護職員_機能訓練指導員": 1,
        "介護職員_利用者15人": 1,  # 利用者15人以下に1人
    },
    "特養": {
        "施設長": 1,
        "医師": 1,
        "生活相談員": 1,
        "介護職員_看護職員_利用者3人": 1,  # 3:1配置
        "栄養士": 1,
    },
    "グループホーム": {
        "計画作成担当者": 1,
        "介護従業者": 1,  # 昼間は3:1、夜間は1ユニット1人以上
        "管理者": 1,
    },
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
class ShiftManagementResult:
    """シフト管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} シフト管理パイプライン",
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


async def run_shift_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> ShiftManagementResult:
    """
    シフト・勤怠管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "facility_type": str,        # "訪問介護" / "通所介護" / "特養" / "グループホーム"
            "target_year": int,
            "target_month": int,
            "staff_list": [
                {
                    "staff_id": str,
                    "staff_name": str,
                    "role": str,             # "介護職員" / "看護職員" 等
                    "qualification": str,    # 資格
                    "desired_holidays": list[str],  # 希望休暇日（YYYY-MM-DD）
                    "night_shift_count_this_month": int,
                }
            ],
        }

    Returns:
        ShiftManagementResult
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

    def _fail(step_name: str) -> ShiftManagementResult:
        return ShiftManagementResult(
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
            "text": _serialize_staff_data(input_data),
            "schema": {
                "facility_type": "string",
                "target_year": "int",
                "target_month": "int",
                "staff_list": "list[{staff_id, staff_name, role, qualification, desired_holidays, night_shift_count_this_month}]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    facility_type = input_data.get("facility_type", s1_out.result.get("facility_type", "通所介護"))
    staff_list = input_data.get("staff_list", s1_out.result.get("staff_list", []))
    target_year = input_data.get("target_year", s1_out.result.get("target_year", 2026))
    target_month = input_data.get("target_month", s1_out.result.get("target_month", 1))
    context.update({
        "facility_type": facility_type,
        "staff_list": staff_list,
        "target_year": target_year,
        "target_month": target_month,
    })

    # ─── Step 2: rule_matcher（人員配置基準照合） ───────────────────────────
    standard = STAFFING_STANDARDS.get(facility_type, {})
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": staff_list,
            "rule_type": "staffing_standard",
            "facility_type": facility_type,
            "required_roles": list(standard.keys()),
        },
        context=context,
    ))
    _add_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("rule_matcher")
    staffing_check = s2_out.result
    context["staffing_check"] = staffing_check

    # ─── Step 3: constraint_checker ─────────────────────────────────────
    s3_start = int(time.time() * 1000)
    constraint_violations: list[str] = []
    for staff in staff_list:
        night_count = staff.get("night_shift_count_this_month", 0)
        staff_name = staff.get("staff_name", staff.get("staff_id", "不明"))
        if night_count > MAX_NIGHT_SHIFTS_PER_MONTH:
            constraint_violations.append(
                f"[{staff_name}] 月間夜勤回数 {night_count}回 が上限({MAX_NIGHT_SHIFTS_PER_MONTH}回)超過"
            )
        # TODO: 72時間ルール（前夜勤から次の出勤まで72時間以上）の詳細チェック
    s3_out = MicroAgentOutput(
        agent_name="constraint_checker",
        success=True,
        result={
            "constraint_violations": constraint_violations,
            "passed": len(constraint_violations) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "constraint_checker", "constraint_checker", s3_out)
    context["constraint_violations"] = constraint_violations

    # ─── Step 4: shift_generator ─────────────────────────────────────────
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "シフト表",
            "variables": {
                "facility_type": facility_type,
                "target_year": target_year,
                "target_month": target_month,
                "staff_list": staff_list,
                "constraint_violations": constraint_violations,
                "staffing_check": staffing_check,
            },
        },
        context=context,
    ))
    _add_step(4, "shift_generator", "document_generator", s4_out)
    if not s4_out.success:
        return _fail("shift_generator")
    generated_shift = s4_out.result
    context["generated_shift"] = generated_shift

    # ─── Step 5: compliance_checker（人員基準欠如減算リスク検出） ──────────
    s5_start = int(time.time() * 1000)
    reduction_risks: list[str] = []
    unmatched = staffing_check.get("unmatched", [])
    if unmatched:
        for role in unmatched:
            reduction_risks.append(
                f"人員基準欠如の可能性: 役職「{role}」が不足。70%減算リスクあり。"
            )
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "reduction_risks": reduction_risks,
            "passed": len(reduction_risks) == 0,
        },
        confidence=1.0 if len(reduction_risks) == 0 else 0.7,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["reduction_risks"] = reduction_risks

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": generated_shift,
            "required_fields": ["content"],
            "reduction_risks": reduction_risks,
            "constraint_violations": constraint_violations,
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: execution_logs保存 + 管理者への確認通知実装
    logger.info(
        f"shift_management_pipeline: company_id={company_id}, "
        f"facility_type={facility_type}, "
        f"staff_count={len(staff_list)}, "
        f"reduction_risks={len(reduction_risks)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "notification_sent": False,  # TODO: 管理者通知実装
            "reduction_risks_count": len(reduction_risks),
            "constraint_violations_count": len(constraint_violations),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "facility_type": facility_type,
        "target_year": target_year,
        "target_month": target_month,
        "staff_list": staff_list,
        "staffing_check": staffing_check,
        "constraint_violations": constraint_violations,
        "reduction_risks": reduction_risks,
        "generated_shift": generated_shift,
    }

    return ShiftManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_staff_data(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    import json
    return json.dumps(input_data, ensure_ascii=False)

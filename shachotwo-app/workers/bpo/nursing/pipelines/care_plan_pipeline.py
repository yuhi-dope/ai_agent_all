"""介護・福祉業 ケアプラン作成支援パイプライン

Steps:
  Step 1: extractor          アセスメント情報構造化（利用者情報・ADL・IADL・認知機能）
  Step 2: needs_analyzer     ニーズ抽出（課題分析表：領域別に生活課題を抽出）
  Step 3: rule_matcher       介護保険サービス照合（要介護度×サービス種別×利用限度額）
  Step 4: plan_generator     第1〜7表ドラフト生成（LLMによる計画書本文生成）
  Step 5: compliance_checker 介護保険法・標準様式チェック（記載必須項目の充足確認）
  Step 6: validator          出力バリデーション（利用者署名欄・同意欄・有効期間等）
  Step 7: saas_writer        execution_logs保存 + ケアマネへの確認通知
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

# 要介護度別の区分支給限度基準額（単位/月）
CARE_LEVEL_LIMIT_UNITS: dict[int, int] = {
    1: 16_765,
    2: 19_705,
    3: 27_048,
    4: 30_938,
    5: 36_217,
}

# ケアプラン必須記載項目（第1表〜第7表）
REQUIRED_PLAN_FIELDS = [
    "利用者氏名",
    "要介護状態区分",
    "居宅サービス計画作成日",
    "計画作成者氏名",
    "長期目標",
    "短期目標",
    "サービス内容",
    "担当者氏名",
    "有効期間",
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
class CarePlanResult:
    """ケアプラン作成支援パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} ケアプラン作成支援パイプライン",
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


async def run_care_plan_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> CarePlanResult:
    """
    ケアプラン作成支援パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "user_info": {
                "user_id": str,
                "user_name": str,
                "care_level": int,       # 要介護度 1〜5
                "age": int,
                "main_disease": str,
                "adl_notes": str,        # ADL/IADL状況メモ
                "cognitive_notes": str,  # 認知機能状況メモ
            },
            "assessment_text": str,  # アセスメント自由記述（任意）
            "target_period_months": int,  # 計画有効期間（月数）
        }

    Returns:
        CarePlanResult
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

    def _fail(step_name: str) -> CarePlanResult:
        return CarePlanResult(
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
            "text": _serialize_assessment(input_data),
            "schema": {
                "user_id": "string",
                "user_name": "string",
                "care_level": "int",
                "age": "int",
                "main_disease": "string",
                "adl_notes": "string",
                "cognitive_notes": "string",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    user_info = input_data.get("user_info", s1_out.result)
    context["user_info"] = user_info

    # ─── Step 2: needs_analyzer（ルールマッチャーでニーズ抽出） ────────────
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": [user_info],
            "rule_type": "care_needs_analysis",
            "adl_notes": user_info.get("adl_notes", ""),
            "cognitive_notes": user_info.get("cognitive_notes", ""),
            "care_level": user_info.get("care_level", 1),
        },
        context=context,
    ))
    _add_step(2, "needs_analyzer", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("needs_analyzer")
    extracted_needs = s2_out.result.get("matched_rules", [])
    context["extracted_needs"] = extracted_needs

    # ─── Step 3: rule_matcher（介護保険サービス照合） ────────────────────
    care_level = user_info.get("care_level", 1)
    limit_units = CARE_LEVEL_LIMIT_UNITS.get(care_level, 16_765)
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": extracted_needs,
            "rule_type": "care_service_match",
            "care_level": care_level,
            "limit_units": limit_units,
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    if not s3_out.success:
        return _fail("rule_matcher")
    matched_services = s3_out.result.get("matched_rules", [])
    context["matched_services"] = matched_services
    context["limit_units"] = limit_units

    # ─── Step 4: plan_generator（第1〜7表ドラフト生成） ─────────────────
    target_period = input_data.get("target_period_months", 6)
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "居宅サービス計画書（第1〜7表）",
            "variables": {
                "user_info": user_info,
                "extracted_needs": extracted_needs,
                "matched_services": matched_services,
                "target_period_months": target_period,
                "limit_units": limit_units,
            },
        },
        context=context,
    ))
    _add_step(4, "plan_generator", "document_generator", s4_out)
    if not s4_out.success:
        return _fail("plan_generator")
    generated_plan = s4_out.result
    context["generated_plan"] = generated_plan

    # ─── Step 5: compliance_checker ─────────────────────────────────────
    s5_start = int(time.time() * 1000)
    compliance_issues: list[str] = []
    plan_content = generated_plan.get("content", "")
    for field_name in REQUIRED_PLAN_FIELDS:
        if field_name not in str(plan_content):
            compliance_issues.append(f"必須記載項目が不足: {field_name}")
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "compliance_issues": compliance_issues,
            "passed": len(compliance_issues) == 0,
        },
        confidence=1.0 if len(compliance_issues) == 0 else 0.8,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["compliance_issues"] = compliance_issues

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": generated_plan,
            "required_fields": ["content"],
            "compliance_issues": compliance_issues,
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: execution_logs保存 + ケアマネへの確認通知実装
    logger.info(
        f"care_plan_pipeline: company_id={company_id}, "
        f"user_id={user_info.get('user_id', '')}, "
        f"compliance_issues={len(compliance_issues)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "notification_sent": False,  # TODO: ケアマネ通知実装
            "compliance_issues_count": len(compliance_issues),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "user_info": user_info,
        "extracted_needs": extracted_needs,
        "matched_services": matched_services,
        "generated_plan": generated_plan,
        "compliance_issues": compliance_issues,
        "limit_units": limit_units,
    }

    return CarePlanResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_assessment(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    import json
    if "assessment_text" in input_data:
        return input_data["assessment_text"]
    return json.dumps(input_data.get("user_info", {}), ensure_ascii=False)

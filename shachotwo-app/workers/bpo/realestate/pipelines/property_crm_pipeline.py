"""不動産業 内見・顧客管理CRMパイプライン

Steps:
  Step 1: inquiry_parser        反響メール/フォームの構造化（顧客名・連絡先・希望条件抽出）
  Step 2: customer_matcher      既存顧客との重複チェック（電話番号/メール）・新規登録
  Step 3: property_matcher      希望条件×物件DBでマッチング（加重スコアリング）
  Step 4: proposal_generator    提案メール/LINE文面のLLM生成（類似物件3件提案）
  Step 5: temperature_updater   顧客温度感スコア更新（加算/減算ルール適用）
  Step 6: action_scheduler      次回アクション（追客）のスケジューリング+DB保存
  Step 7: output_validator      バリデーション（提案内容・スケジュール整合性チェック）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.message import run_message_drafter
from workers.micro.calculator import run_cost_calculator
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 顧客温度感スコアの加算ルール
TEMPERATURE_SCORE_RULES: dict[str, int] = {
    "inquiry_sent":         30,
    "replied":              20,
    "viewing_booked":       30,
    "viewing_done":         10,
    "second_viewing":       15,
    "application_intent":   20,
    "price_negotiation":    10,
    "email_opened":          5,
    "property_page_viewed":  3,
    "day_passed":           -2,  # 最大-30
    "day_passed_max":       -30,
}

# 温度感ラベルのしきい値
TEMPERATURE_LABELS: list[tuple[int, str]] = [
    (80, "hot"),
    (50, "warm"),
    (20, "cool"),
    (0,  "cold"),
]

# 温度感別の次回アクション（日数）
FOLLOW_UP_SCHEDULE: dict[str, dict[str, Any]] = {
    "hot":  {"action": "immediate_follow_up", "days": 0},
    "warm": {"action": "property_proposal_email", "days": 3},
    "cool": {"action": "new_listing_notification", "days": 7},
    "cold": {"action": "revival_email", "days": 30},
}

# 物件マッチングスコアの重み
MATCHING_WEIGHTS: dict[str, float] = {
    "area":              0.30,
    "price_budget":      0.25,
    "floor_plan":        0.15,
    "building_area":     0.10,
    "station_distance":  0.10,
    "building_age":      0.05,
    "facilities":        0.05,
}

# マッチングスコアのしきい値
MATCH_AUTO_PROPOSE_THRESHOLD = 0.70  # 自動提案対象
MATCH_ADDITIONAL_THRESHOLD   = 0.50  # 追加候補


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
class PropertyCrmResult:
    """内見・顧客管理CRMパイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 内見・顧客管理CRMパイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        temp = self.final_output.get("temperature_label", "")
        score = self.final_output.get("temperature_score", 0)
        lines.append(f"  顧客温度感: {temp}（{score}点）")
        matched = self.final_output.get("matched_properties", [])
        lines.append(f"  マッチング物件: {len(matched)}件")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _calc_temperature_label(score: int) -> str:
    """温度感スコアからラベルを決定する。"""
    for threshold, label in TEMPERATURE_LABELS:
        if score >= threshold:
            return label
    return "cold"


async def run_property_crm_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> PropertyCrmResult:
    """
    内見・顧客管理CRMパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "inquiry_source": str,         # suumo / homes / athome / hp / line / phone / walk_in
            "inquiry_text": str,           # 反響メール/フォームのテキスト
            "inquiry_timestamp": str,      # 反響受信日時（ISO8601）
            "existing_customer_id": str,   # 既存顧客ID（判明している場合）
            "action_type": str,            # new_inquiry / viewing_feedback / follow_up_event
            "action_detail": dict,         # アクション詳細（viewing_feedback等）
            "property_db": list[dict],     # マッチング対象の物件リスト
        }

    Returns:
        PropertyCrmResult
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

    def _fail(step_name: str) -> PropertyCrmResult:
        return PropertyCrmResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: inquiry_parser ──────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": input_data.get("inquiry_text", ""),
            "schema": {
                "customer_name": "str",
                "phone": "str",
                "email": "str",
                "transaction_type": "str",  # buy / rent
                "preferred_areas": "list",
                "budget_min": "int",
                "budget_max": "int",
                "floor_plans": "list",
                "min_area": "float",
                "max_station_distance": "int",
                "move_in_timing": "str",
                "required_facilities": "list",
                "message": "str",
            },
            "purpose": "反響メール・問合せフォームの構造化抽出",
        },
        context=context,
    ))
    _add_step(1, "inquiry_parser", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("inquiry_parser")
    structured_inquiry = s1_out.result
    context["structured_inquiry"] = structured_inquiry

    # ─── Step 2: customer_matcher ────────────────────────────────────────
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "customer_dedup",
            "new_customer": {
                "name": structured_inquiry.get("customer_name", ""),
                "phone": structured_inquiry.get("phone", ""),
                "email": structured_inquiry.get("email", ""),
            },
            "existing_customer_id": input_data.get("existing_customer_id"),
            "match_fields": ["phone", "email"],
        },
        context=context,
    ))
    _add_step(2, "customer_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("customer_matcher")
    customer_record = s2_out.result
    is_new_customer = customer_record.get("is_new", True)
    current_temperature_score = customer_record.get("temperature_score", 30)
    context["customer_record"] = customer_record
    context["is_new_customer"] = is_new_customer

    # ─── Step 3: property_matcher ────────────────────────────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "property_matching",
            "customer_preferences": {
                "preferred_areas": structured_inquiry.get("preferred_areas", []),
                "budget_min": structured_inquiry.get("budget_min", 0),
                "budget_max": structured_inquiry.get("budget_max", 0),
                "floor_plans": structured_inquiry.get("floor_plans", []),
                "min_area": structured_inquiry.get("min_area", 0),
                "max_station_distance": structured_inquiry.get("max_station_distance", 15),
                "required_facilities": structured_inquiry.get("required_facilities", []),
            },
            "property_db": input_data.get("property_db", []),
            "weights": MATCHING_WEIGHTS,
            "auto_propose_threshold": MATCH_AUTO_PROPOSE_THRESHOLD,
            "additional_threshold": MATCH_ADDITIONAL_THRESHOLD,
            "top_n": 3,
        },
        context=context,
    ))
    _add_step(3, "property_matcher", "rule_matcher", s3_out)
    if not s3_out.success:
        return _fail("property_matcher")
    matched_properties = s3_out.result.get("matched", [])
    context["matched_properties"] = matched_properties

    # ─── Step 4: proposal_generator ──────────────────────────────────────
    s4_out = await run_message_drafter(
        document_type="物件提案メール",
        context={
            "customer_name": structured_inquiry.get("customer_name", "お客様"),
            "source": input_data.get("inquiry_source", ""),
            "matched_properties": matched_properties[:3],
            "preferences": structured_inquiry,
            "is_new_customer": is_new_customer,
        },
        company_id=company_id,
    )
    # MicroAgentOutput 形式に変換
    s4_start_ts = int(time.time() * 1000)
    s4_out_obj = MicroAgentOutput(
        agent_name="message_drafter",
        success=True,
        result={
            "subject": s4_out.subject,
            "body": s4_out.body,
            "model_used": s4_out.model_used,
            "is_template_fallback": s4_out.is_template_fallback,
        },
        confidence=0.85,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start_ts,
    )
    _add_step(4, "proposal_generator", "message_drafter", s4_out_obj)
    proposal_message = s4_out_obj.result
    context["proposal_message"] = proposal_message

    # ─── Step 5: temperature_updater ─────────────────────────────────────
    action_type = input_data.get("action_type", "new_inquiry")
    score_delta = TEMPERATURE_SCORE_RULES.get(
        action_type, TEMPERATURE_SCORE_RULES.get("inquiry_sent", 30)
    )
    # 日数経過の減算
    last_contact_days = input_data.get("days_since_last_contact", 0)
    score_decay = max(
        TEMPERATURE_SCORE_RULES["day_passed"] * last_contact_days,
        TEMPERATURE_SCORE_RULES["day_passed_max"],
    )
    new_score = min(100, max(0, current_temperature_score + score_delta + score_decay))
    new_label = _calc_temperature_label(new_score)

    s5_start = int(time.time() * 1000)
    s5_out = MicroAgentOutput(
        agent_name="cost_calculator",
        success=True,
        result={
            "previous_score": current_temperature_score,
            "score_delta": score_delta,
            "score_decay": score_decay,
            "new_score": new_score,
            "temperature_label": new_label,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "temperature_updater", "cost_calculator", s5_out)
    context["temperature_score"] = new_score
    context["temperature_label"] = new_label

    # ─── Step 6: action_scheduler ────────────────────────────────────────
    follow_up_plan = FOLLOW_UP_SCHEDULE.get(new_label, FOLLOW_UP_SCHEDULE["cool"])
    s6_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "write_type": "crm_action_schedule",
            "customer_id": customer_record.get("customer_id", ""),
            "temperature_score": new_score,
            "temperature_label": new_label,
            "proposal_message": proposal_message,
            "follow_up_plan": follow_up_plan,
            "matched_properties": matched_properties,
            "inquiry_source": input_data.get("inquiry_source", ""),
            "is_new_customer": is_new_customer,
        },
        context=context,
    ))
    _add_step(6, "action_scheduler", "saas_writer", s6_out)
    if not s6_out.success:
        logger.warning("[property_crm] スケジュール保存失敗 — 処理は続行")

    # ─── Step 7: output_validator ─────────────────────────────────────────
    s7_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "customer_record": customer_record,
                "matched_properties": matched_properties,
                "proposal_message": proposal_message,
                "temperature_score": new_score,
            },
            "required_fields": ["customer_record", "matched_properties", "proposal_message"],
            "check_type": "crm_completeness",
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", s7_out)

    final_output = {
        "structured_inquiry": structured_inquiry,
        "customer_record": customer_record,
        "is_new_customer": is_new_customer,
        "matched_properties": matched_properties,
        "proposal_message": proposal_message,
        "temperature_score": new_score,
        "temperature_label": new_label,
        "follow_up_plan": follow_up_plan,
        "scheduled_action": s6_out.result,
        "validation": s7_out.result,
    }

    return PropertyCrmResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

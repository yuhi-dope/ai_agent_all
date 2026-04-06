"""不動産業 修繕・設備管理パイプライン

Steps:
  Step 1: request_parser        修繕依頼の構造化（テキスト+写真からカテゴリ・箇所・状況抽出）
  Step 2: urgency_classifier    緊急度AI判定（キーワードマッチ+季節/時間帯/入居者属性補正）
  Step 3: vendor_matcher        協力業者マッチング（地域×工種×稼働状況×過去評価）
  Step 4: estimate_requester    見積依頼の自動送信（業者向けメール/FAX文面生成）
  Step 5: owner_notifier        オーナーへの承認依頼（緊急Level1-2は事後報告）
  Step 6: schedule_coordinator  施工日程調整+DB保存（入居者・業者・管理会社三者調整）
  Step 7: completion_recorder   完了記録・修繕履歴更新+長期修繕計画への反映
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.message import run_message_drafter
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 緊急度キーワード辞書
URGENCY_KEYWORDS: dict[int, list[str]] = {
    1: [  # 緊急（2時間以内）
        "漏水", "水漏れ", "ガス漏れ", "ガス臭", "停電", "断水",
        "トイレ詰まり", "鍵紛失", "帰宅できない", "火災報知器",
        "窓ガラス割れ", "割れた", "煙", "異臭",
    ],
    2: [  # 至急（24時間以内）
        "エアコン故障", "エアコンが故障", "給湯器故障", "給湯器が故障", "お湯が出ない", "排水不良",
        "インターホン故障", "オートロック故障", "エレベーター停止",
        "エレベーター動かない",
    ],
    3: [  # 通常（1週間以内）
        "壁紙剥がれ", "床のきしみ", "蛇口パッキン", "網戸破れ",
        "換気扇異音", "照明交換", "雨漏り", "ドア閉まらない",
    ],
    4: [  # 定期/美観（次回点検時）
        "外壁汚れ", "共用部美観", "植栽", "駐輪場",
        "掲示板", "ポスト汚れ",
    ],
}

# 緊急度の対応期限（時間）
URGENCY_DEADLINES: dict[int, str] = {
    1: "2時間以内",
    2: "24時間以内",
    3: "1週間以内",
    4: "次回定期点検時",
}

# 夜間時間帯（緊急度昇格チェック）
NIGHT_HOURS = (22, 6)  # 22:00-6:00

# 長期修繕計画の部位別修繕周期
REPAIR_CYCLE: dict[str, dict[str, Any]] = {
    "屋上防水":    {"cycle_years": 13, "cost_per_sqm": 6000},
    "外壁塗装":    {"cycle_years": 13, "cost_per_sqm": 4500},
    "外壁タイル":  {"cycle_years": 13, "cost_per_sqm": 3500},
    "鉄部塗装":    {"cycle_years":  6, "cost_per_sqm":  1000},
    "給水管更新":  {"cycle_years": 27, "cost_per_sqm": 11500},
    "排水管更新":  {"cycle_years": 27, "cost_per_sqm":  9000},
    "消防設備更新": {"cycle_years": 22, "cost_per_sqm":  2000},
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
class RepairManagementResult:
    """修繕・設備管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 修繕・設備管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        urgency = self.final_output.get("urgency_level")
        deadline = self.final_output.get("urgency_deadline", "")
        lines.append(f"  緊急度: Level {urgency} ({deadline})")
        vendor = self.final_output.get("assigned_vendor", {})
        if vendor:
            lines.append(f"  手配業者: {vendor.get('name', '未定')}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _detect_urgency_from_keywords(description: str) -> tuple[int, float]:
    """
    キーワードマッチングで緊急度を判定する。

    Returns:
        (urgency_level, confidence)
    """
    desc_lower = description.lower()
    for level in [1, 2, 3, 4]:
        for kw in URGENCY_KEYWORDS[level]:
            if kw in desc_lower or kw in description:
                return level, 0.85
    return 3, 0.50  # デフォルト: 通常


def _apply_context_correction(
    base_level: int,
    request_hour: int,
    season_month: int,
    category: str,
    tenant_is_elderly: bool = False,
) -> int:
    """
    コンテキストによる緊急度の補正。
    - 夜間の漏水 → Level 1に昇格
    - 真夏/真冬のエアコン故障 → Level 2に昇格
    - 高齢者世帯 → 1段階昇格
    """
    corrected = base_level
    # 夜間（22:00-6:00）の漏水・ガス系はLevel1に
    is_night = (request_hour >= NIGHT_HOURS[0] or request_hour < NIGHT_HOURS[1])
    if is_night and base_level <= 2 and category in ("plumbing", "gas"):
        corrected = 1
    # 真夏(7-9月)/真冬(12-2月)のエアコン故障はLevel2に
    is_hot_season = season_month in (7, 8, 9)
    is_cold_season = season_month in (12, 1, 2)
    if category == "hvac" and (is_hot_season or is_cold_season):
        corrected = min(corrected, 2)
    # 高齢者・障害者は1段階昇格
    if tenant_is_elderly:
        corrected = max(corrected - 1, 1)
    return corrected


async def run_repair_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> RepairManagementResult:
    """
    修繕・設備管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "property_id": str,            # 物件ID
            "tenant_id": str,              # 入居者ID（任意）
            "room_number": str,            # 部屋番号
            "description": str,            # 修繕依頼の内容テキスト
            "photo_urls": list[str],       # 写真URLのリスト
            "request_timestamp": str,      # 依頼受付日時（ISO8601）
            "tenant_is_elderly": bool,     # 高齢者・障害者世帯か
            "vendor_db": list[dict],       # 協力業者リスト
            "completion_data": dict,       # 完了記録（完了時のみ）
        }

    Returns:
        RepairManagementResult
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

    def _fail(step_name: str) -> RepairManagementResult:
        return RepairManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: request_parser ──────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": input_data.get("description", ""),
            "schema": {
                "category": "str",         # plumbing / electrical / hvac / structural / cosmetic / equipment / gas
                "location": "str",         # 修繕箇所（風呂/キッチン/玄関 等）
                "situation": "str",        # 状況の詳細
                "symptom": "str",          # 症状（漏れている/動かない等）
                "impact_area": "str",      # 影響範囲（専有部/共用部）
            },
            "purpose": "修繕依頼の構造化（カテゴリ・箇所・症状抽出）",
        },
        context=context,
    ))
    _add_step(1, "request_parser", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("request_parser")
    structured_request = {**s1_out.result}
    structured_request["description"] = input_data.get("description", "")
    context["structured_request"] = structured_request

    # ─── Step 2: urgency_classifier ──────────────────────────────────────
    # キーワードマッチングで初期緊急度を判定
    base_urgency, kw_confidence = _detect_urgency_from_keywords(
        input_data.get("description", "")
    )
    # コンテキスト補正
    request_ts = input_data.get("request_timestamp", "")
    try:
        request_dt = datetime.fromisoformat(request_ts.replace("Z", "+00:00"))
        request_hour = request_dt.hour
        season_month = request_dt.month
    except (ValueError, AttributeError):
        request_hour = 12
        season_month = datetime.now().month
    final_urgency = _apply_context_correction(
        base_level=base_urgency,
        request_hour=request_hour,
        season_month=season_month,
        category=structured_request.get("category", ""),
        tenant_is_elderly=input_data.get("tenant_is_elderly", False),
    )
    urgency_deadline = URGENCY_DEADLINES[final_urgency]

    s2_start = int(time.time() * 1000)
    s2_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "base_urgency": base_urgency,
            "final_urgency": final_urgency,
            "deadline": urgency_deadline,
            "context_corrected": final_urgency != base_urgency,
            "request_hour": request_hour,
            "season_month": season_month,
        },
        confidence=kw_confidence,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "urgency_classifier", "rule_matcher", s2_out)
    context["urgency_level"] = final_urgency
    context["urgency_deadline"] = urgency_deadline

    # ─── Step 3: vendor_matcher ───────────────────────────────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "vendor_matching",
            "category": structured_request.get("category", ""),
            "urgency_level": final_urgency,
            "property_id": input_data.get("property_id", ""),
            "vendor_db": input_data.get("vendor_db", []),
            "match_criteria": ["area", "specialty", "availability", "past_rating"],
        },
        context=context,
    ))
    _add_step(3, "vendor_matcher", "rule_matcher", s3_out)
    if not s3_out.success:
        return _fail("vendor_matcher")
    matched_vendor = s3_out.result.get("vendor", {})
    context["matched_vendor"] = matched_vendor

    # ─── Step 4: estimate_requester ──────────────────────────────────────
    estimate_message = await run_message_drafter(
        document_type="修繕見積依頼",
        context={
            "vendor_name": matched_vendor.get("name", "業者"),
            "property_id": input_data.get("property_id", ""),
            "room_number": input_data.get("room_number", ""),
            "category": structured_request.get("category", ""),
            "description": structured_request.get("description", ""),
            "location": structured_request.get("location", ""),
            "urgency_level": final_urgency,
            "urgency_deadline": urgency_deadline,
        },
        company_id=company_id,
    )
    s4_start = int(time.time() * 1000)
    s4_out = MicroAgentOutput(
        agent_name="message_drafter",
        success=True,
        result={
            "subject": estimate_message.subject,
            "body": estimate_message.body,
            "is_template_fallback": estimate_message.is_template_fallback,
        },
        confidence=0.85,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "estimate_requester", "message_drafter", s4_out)
    context["estimate_request"] = s4_out.result

    # ─── Step 5: owner_notifier ──────────────────────────────────────────
    # Level1-2は事後報告、Level3-4はオーナー承認を先行取得
    notification_type = (
        "緊急修繕事後報告" if final_urgency <= 2 else "修繕承認依頼"
    )
    owner_message = await run_message_drafter(
        document_type=notification_type,
        context={
            "property_id": input_data.get("property_id", ""),
            "room_number": input_data.get("room_number", ""),
            "description": structured_request.get("description", ""),
            "urgency_level": final_urgency,
            "urgency_deadline": urgency_deadline,
            "vendor_name": matched_vendor.get("name", ""),
            "estimated_cost": matched_vendor.get("estimated_cost", 0),
            "is_emergency": final_urgency <= 2,
        },
        company_id=company_id,
    )
    s5_start = int(time.time() * 1000)
    s5_out = MicroAgentOutput(
        agent_name="message_drafter",
        success=True,
        result={
            "notification_type": notification_type,
            "subject": owner_message.subject,
            "body": owner_message.body,
            "requires_approval": final_urgency > 2,
            "is_template_fallback": owner_message.is_template_fallback,
        },
        confidence=0.85,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "owner_notifier", "message_drafter", s5_out)
    context["owner_notification"] = s5_out.result

    # ─── Step 6: schedule_coordinator ────────────────────────────────────
    s6_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "write_type": "repair_schedule",
            "property_id": input_data.get("property_id", ""),
            "tenant_id": input_data.get("tenant_id", ""),
            "room_number": input_data.get("room_number", ""),
            "category": structured_request.get("category", ""),
            "urgency_level": final_urgency,
            "vendor_id": matched_vendor.get("vendor_id", ""),
            "estimate_request": s4_out.result,
            "owner_notification": s5_out.result,
            "status": "vendor_assigned" if matched_vendor else "assessing",
        },
        context=context,
    ))
    _add_step(6, "schedule_coordinator", "saas_writer", s6_out)
    if not s6_out.success:
        logger.warning("[repair_management] 日程調整DB保存失敗 — 処理は続行")

    # ─── Step 7: completion_recorder ─────────────────────────────────────
    completion_data = input_data.get("completion_data")
    if completion_data:
        s7_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "write_type": "repair_completion",
                "property_id": input_data.get("property_id", ""),
                "actual_cost": completion_data.get("actual_cost", 0),
                "completed_date": completion_data.get("completed_date", ""),
                "component": structured_request.get("category", ""),
                "update_repair_plan": True,
                "repair_cycle_data": REPAIR_CYCLE.get(
                    structured_request.get("location", ""), {}
                ),
            },
            context=context,
        ))
    else:
        s7_start = int(time.time() * 1000)
        s7_out = MicroAgentOutput(
            agent_name="output_validator",
            success=True,
            result={"skipped": True, "reason": "完了記録未提供（施工前）"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )
    _add_step(7, "completion_recorder", "saas_writer", s7_out)

    final_output = {
        "structured_request": structured_request,
        "urgency_level": final_urgency,
        "urgency_deadline": urgency_deadline,
        "assigned_vendor": matched_vendor,
        "estimate_request": s4_out.result,
        "owner_notification": s5_out.result,
        "schedule_result": s6_out.result,
        "completion_record": s7_out.result,
    }

    return RepairManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

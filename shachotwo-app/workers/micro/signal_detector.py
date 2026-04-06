"""signal_detector マイクロエージェント。シグナル温度判定 + 自動フォローアップアクション決定。"""
import logging
import time
from typing import Any

from pydantic import BaseModel

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------

class SignalEvent(BaseModel):
    """検出対象のシグナルイベント"""
    event_type: str  # cta_click / schedule_confirmed / doc_download / lp_view
    company_id: str
    metadata: dict[str, Any] = {}


class SignalClassification(BaseModel):
    """シグナルの温度・推奨アクション"""
    temperature: str  # hot / confirmed / warm / cold
    action: str  # schedule / create_meeting / notify_and_followup / followup / retry / ignore


class FollowupAction(BaseModel):
    """自動フォローアクション"""
    company_id: str
    action: str  # send_followup / send_whitepaper / retry_different_angle / none
    delay_days: int
    template: str = ""


# ---------------------------------------------------------------------------
# シグナル温度判定
# ---------------------------------------------------------------------------

_SIGNAL_RULES: dict[str, SignalClassification] = {
    "cta_click": SignalClassification(temperature="hot", action="schedule"),
    "schedule_confirmed": SignalClassification(temperature="confirmed", action="create_meeting"),
    "doc_download": SignalClassification(temperature="warm", action="notify_and_followup"),
}


def classify_signal(event: SignalEvent) -> SignalClassification:
    """イベントタイプから温度とアクションを判定"""
    if event.event_type in _SIGNAL_RULES:
        return _SIGNAL_RULES[event.event_type]

    # LP閲覧は滞在時間で判定
    if event.event_type == "lp_view":
        duration = event.metadata.get("duration_sec", 0)
        if duration >= 30:
            return SignalClassification(temperature="warm", action="followup")
        if duration <= 3:
            return SignalClassification(temperature="cold", action="retry")

    return SignalClassification(temperature="cold", action="ignore")


# ---------------------------------------------------------------------------
# 自動フォローアップ決定
# ---------------------------------------------------------------------------

def get_auto_followups(signals: list[dict[str, Any]]) -> list[FollowupAction]:
    """シグナルリストから自動フォローアクションを生成"""
    actions: list[FollowupAction] = []

    for sig in signals:
        temp = sig.get("temperature", "cold")
        company_id = sig.get("company_id", "")

        if temp == "hot":
            continue  # HOTは日程調整へ（フォロー不要）

        if temp == "warm":
            if sig.get("event_type") == "doc_download":
                actions.append(FollowupAction(
                    company_id=company_id,
                    action="send_followup",
                    delay_days=3,
                    template="warm_doc_download",
                ))
            elif sig.get("event_type") == "lp_view":
                actions.append(FollowupAction(
                    company_id=company_id,
                    action="send_followup",
                    delay_days=1,
                    template="warm_lp_view",
                ))

        elif temp == "cold":
            actions.append(FollowupAction(
                company_id=company_id,
                action="retry_different_angle",
                delay_days=7,
                template="cold_retry",
            ))

    return actions


# ---------------------------------------------------------------------------
# マイクロエージェント run 関数
# ---------------------------------------------------------------------------

async def run_signal_detector(input: MicroAgentInput) -> MicroAgentOutput:
    """
    シグナルイベントの温度判定 + フォローアップアクション決定を行う。

    payload:
        events (list[dict]): シグナルイベントのリスト
            各要素: event_type (str), company_id (str), metadata (dict)

    result:
        classifications (list[dict]): 各イベントの温度判定結果
        followup_actions (list[dict]): 自動フォローアクション
        summary (dict): 温度別件数サマリ
    """
    start_ms = int(time.time() * 1000)
    agent_name = "signal_detector"

    try:
        events_raw = input.payload.get("events", [])
        if not events_raw:
            raise MicroAgentError(agent_name, "input_validation", "events が空です")

        # 温度判定
        classifications: list[dict[str, Any]] = []
        classified_signals: list[dict[str, Any]] = []
        for ev_data in events_raw:
            event = SignalEvent(**ev_data)
            classification = classify_signal(event)
            classifications.append({
                "event_type": event.event_type,
                "company_id": event.company_id,
                "temperature": classification.temperature,
                "action": classification.action,
            })
            classified_signals.append({
                "event_type": event.event_type,
                "company_id": event.company_id,
                "temperature": classification.temperature,
            })

        # フォローアップ決定
        followups = get_auto_followups(classified_signals)

        # サマリ
        temps = [c["temperature"] for c in classifications]
        summary = {
            "hot": temps.count("hot"),
            "confirmed": temps.count("confirmed"),
            "warm": temps.count("warm"),
            "cold": temps.count("cold"),
            "total": len(temps),
        }

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "classifications": classifications,
                "followup_actions": [f.model_dump() for f in followups],
                "summary": summary,
            },
            confidence=1.0,  # ルールベースなので確信度は常に1.0
            cost_yen=0.0,    # LLM不使用
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"signal_detector error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

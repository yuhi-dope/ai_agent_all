"""シグナル温度判定ロジック"""

from __future__ import annotations

from pydantic import BaseModel


class SignalEvent(BaseModel):
    event_type: str  # cta_click / schedule_confirmed / doc_download / lp_view
    company_id: str
    metadata: dict = {}


class SignalClassification(BaseModel):
    temperature: str  # hot / confirmed / warm / cold
    action: str  # schedule / notify_and_followup / followup / retry / ignore


def classify_signal(event: SignalEvent) -> SignalClassification:
    """イベントタイプから温度とアクションを判定"""
    rules = {
        "cta_click": SignalClassification(temperature="hot", action="schedule"),
        "schedule_confirmed": SignalClassification(temperature="confirmed", action="create_meeting"),
        "doc_download": SignalClassification(temperature="warm", action="notify_and_followup"),
    }

    if event.event_type in rules:
        return rules[event.event_type]

    # LP閲覧は滞在時間で判定
    if event.event_type == "lp_view":
        duration = event.metadata.get("duration_sec", 0)
        if duration >= 30:
            return SignalClassification(temperature="warm", action="followup")
        if duration <= 3:
            return SignalClassification(temperature="cold", action="retry")

    return SignalClassification(temperature="cold", action="ignore")

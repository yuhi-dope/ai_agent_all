"""契約前ナーチャリング — 検討中企業の自動フォロー"""

from __future__ import annotations

from datetime import datetime, timedelta
from pydantic import BaseModel


class NurtureClassification(BaseModel):
    temperature: str  # hot / warm / cool / lost
    sequence: list[dict] = []  # フォローメールスケジュール


class NurtureTask(BaseModel):
    deal_id: str
    company_data: dict
    action: str  # send_case_study / send_offer / send_final / send_survey
    send_at: datetime


def classify_post_meeting(signal: str) -> NurtureClassification:
    """商談後の温度分類"""
    if signal in ("contract", "yes", "即契約"):
        return NurtureClassification(temperature="hot")

    if signal in ("検討", "社内で検討", "考えます"):
        return NurtureClassification(
            temperature="warm",
            sequence=[
                {"action": "send_case_study", "delay_days": 3},
                {"action": "send_offer", "delay_days": 7},
                {"action": "send_final", "delay_days": 14},
            ],
        )

    if signal in ("もう少し", "様子見", "時期尚早"):
        return NurtureClassification(
            temperature="cool",
            sequence=[
                {"action": "send_whitepaper", "delay_days": 0},
                {"action": "send_followup", "delay_days": 30},
            ],
        )

    # LOST
    return NurtureClassification(
        temperature="lost",
        sequence=[{"action": "send_survey", "delay_days": 1}],
    )


def schedule_nurture_sequence(deal_id: str, classification: NurtureClassification) -> list[NurtureTask]:
    """フォローメールスケジュール生成"""
    now = datetime.now()
    tasks = []
    for step in classification.sequence:
        tasks.append(NurtureTask(
            deal_id=deal_id,
            company_data={},
            action=step["action"],
            send_at=now + timedelta(days=step["delay_days"]),
        ))
    return tasks


def get_pending_nurtures(all_tasks: list[NurtureTask]) -> list[NurtureTask]:
    """送信期限が来たタスクを取得"""
    now = datetime.now()
    return [t for t in all_tasks if t.send_at <= now]

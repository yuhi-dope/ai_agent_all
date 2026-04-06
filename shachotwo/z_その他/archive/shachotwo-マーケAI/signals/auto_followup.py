"""温度に応じた自動フォロー判定"""

from __future__ import annotations

from pydantic import BaseModel


class FollowupAction(BaseModel):
    company_id: str
    action: str  # send_followup / send_whitepaper / retry_different_angle / none
    delay_days: int
    template: str = ""


def get_auto_followups(signals: list[dict]) -> list[FollowupAction]:
    """シグナルから自動フォローアクションを生成"""
    actions = []

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

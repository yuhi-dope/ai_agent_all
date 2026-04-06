"""フォローメールシーケンス管理"""

from __future__ import annotations

from datetime import datetime, timedelta
from pydantic import BaseModel


class FollowupTask(BaseModel):
    company_id: str
    company_data: dict
    sequence_num: int  # 1=3日後, 2=7日後
    send_after: datetime
    lp_viewed: bool = False


def get_pending_followups(outreach_logs: list[dict], page_views: list[dict]) -> list[FollowupTask]:
    """送信後3日/7日経過した企業のフォロー対象を抽出

    判定ロジック:
    - LP閲覧あり → 3日後ホワイトペーパー、7日後トライアル
    - LP閲覧なし → 3日後件名変更再送、7日後最終
    - 「話を聞きたい」済み → フォロー停止
    """
    tasks: list[FollowupTask] = []
    now = datetime.now()

    viewed_companies = {pv["company_id"] for pv in page_views}
    # CTA済み企業（フォロー停止）
    cta_companies = {pv["company_id"] for pv in page_views if pv.get("cta_clicked")}

    for log in outreach_logs:
        if log.get("action") != "sent":
            continue
        company_id = log["company_id"]
        if company_id in cta_companies:
            continue

        sent_at = datetime.fromisoformat(log["sent_at"]) if isinstance(log["sent_at"], str) else log["sent_at"]
        lp_viewed = company_id in viewed_companies

        # 3日後フォロー
        if now >= sent_at + timedelta(days=3) and log.get("followup_1_sent") is not True:
            tasks.append(FollowupTask(
                company_id=company_id,
                company_data=log.get("company_data", {}),
                sequence_num=1,
                send_after=sent_at + timedelta(days=3),
                lp_viewed=lp_viewed,
            ))

        # 7日後フォロー
        if now >= sent_at + timedelta(days=7) and log.get("followup_2_sent") is not True:
            tasks.append(FollowupTask(
                company_id=company_id,
                company_data=log.get("company_data", {}),
                sequence_num=2,
                send_after=sent_at + timedelta(days=7),
                lp_viewed=lp_viewed,
            ))

    return tasks

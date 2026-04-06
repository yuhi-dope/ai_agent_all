"""パイプライン管理ロジック"""

from __future__ import annotations

from crm.db import get_client
from crm.models import Lead, OutreachLog, PageView, Deal


def create_lead(company_id: str, contact_name: str, phone: str, source: str = "lp_cta") -> Lead:
    """リード作成"""
    client = get_client()
    data = {"company_id": company_id, "contact_name": contact_name, "phone": phone, "source": source, "temperature": "hot", "status": "new"}
    result = client.table("apo_leads").insert(data).execute()
    row = result.data[0]
    return Lead(**row)


def update_deal_stage(deal_id: str, stage: str) -> Deal:
    """商談ステージ更新"""
    client = get_client()
    result = client.table("apo_deals").update({"stage": stage}).eq("id", deal_id).execute()
    return Deal(**result.data[0])


def log_outreach(company_id: str, channel: str, action: str, subject: str = "", body_preview: str = "") -> OutreachLog:
    """アウトリーチ履歴記録"""
    client = get_client()
    data = {"company_id": company_id, "channel": channel, "action": action, "subject": subject, "body_preview": body_preview[:200]}
    result = client.table("apo_outreach_logs").insert(data).execute()
    return OutreachLog(**result.data[0])


def log_page_view(company_id: str, page_url: str, duration_sec: int, cta_clicked: bool = False, doc_downloaded: bool = False) -> PageView:
    """LP閲覧ログ記録"""
    client = get_client()
    data = {"company_id": company_id, "page_url": page_url, "duration_sec": duration_sec, "cta_clicked": cta_clicked, "doc_downloaded": doc_downloaded}
    result = client.table("apo_page_views").insert(data).execute()
    return PageView(**result.data[0])


def create_deal(lead_id: str, company_id: str, meeting_date: str, meeting_url: str) -> Deal:
    """商談作成"""
    client = get_client()
    data = {"lead_id": lead_id, "company_id": company_id, "stage": "appointment", "meeting_date": meeting_date, "meeting_url": meeting_url}
    result = client.table("apo_deals").insert(data).execute()
    return Deal(**result.data[0])

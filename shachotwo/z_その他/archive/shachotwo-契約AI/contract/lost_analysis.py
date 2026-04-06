"""失注理由収集・分析"""

from __future__ import annotations

from datetime import date, timedelta

from supabase import create_client

from config import settings


def _get_client():
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def send_lost_survey(deal_id: str, company_data: dict, to_email: str) -> None:
    """失注理由ヒアリングメール送信"""
    from contract.onboarding import _send_email
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("contract/templates/emails"))
    template = env.get_template("lost_survey.html")
    html = template.render(
        company_name=company_data.get("name", ""),
        survey_url=f"{settings.lp_base_url}/survey/{deal_id}",
    )
    _send_email(
        to=to_email,
        subject=f"{company_data.get('name', '')}様 — ご検討結果についてのお伺い",
        body_html=html,
    )


def record_lost_reason(deal_id: str, category: str, detail: str = "", competitor: str = "") -> None:
    """失注理由をDBに記録"""
    client = _get_client()
    retry = date.today() + timedelta(days=180)  # 6ヶ月後リトライ
    client.table("apo_lost_reasons").insert({
        "deal_id": deal_id,
        "reason_category": category,
        "reason_detail": detail,
        "competitor_name": competitor,
        "retry_date": retry.isoformat(),
    }).execute()


def schedule_retry(deal_id: str, months: int = 6) -> None:
    """リトライ日を設定"""
    client = _get_client()
    retry = date.today() + timedelta(days=months * 30)
    client.table("apo_lost_reasons").update({"retry_date": retry.isoformat()}).eq("deal_id", deal_id).execute()

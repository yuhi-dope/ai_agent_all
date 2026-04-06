"""確認メール送信（双方）"""

from __future__ import annotations

from outreach.email_sender import send_email
from scheduling.calendar_api import MeetingInfo
from config import settings


def send_confirmation_to_prospect(meeting: MeetingInfo, company: dict, contact: dict) -> None:
    """相手に確認メール送信"""
    html = f"""
    <p>{company.get('name', '')} {contact.get('name', '')}様</p>
    <p>下記の日程でお打ち合わせを承りました。</p>
    <table border="1" cellpadding="8" style="border-collapse:collapse;">
        <tr><td>日時</td><td>{meeting.start.strftime('%Y年%m月%d日 %H:%M')}〜{meeting.end.strftime('%H:%M')}</td></tr>
        <tr><td>方法</td><td>Google Meet</td></tr>
        <tr><td>URL</td><td><a href="{meeting.meet_url}">{meeting.meet_url}</a></td></tr>
    </table>
    <p>当日はAI社員「シャチョツー」が御社の業務を<br>
    どのようにサポートできるかご説明いたします。</p>
    <p>{settings.sender_name}<br>{settings.company_name}</p>
    """

    send_email(
        to=contact.get("email", ""),
        subject=f"{meeting.start.strftime('%m/%d')} お打ち合わせのご確認",
        body_html=html,
    )


def send_notification_to_owner(meeting: MeetingInfo, company: dict, contact: dict) -> None:
    """杉本に通知"""
    from signals.gmail_notifier import notify_hot_lead
    notify_hot_lead(
        company=company,
        contact=contact,
        meeting={"date": meeting.start.strftime("%m/%d %H:%M"), "url": meeting.meet_url},
    )

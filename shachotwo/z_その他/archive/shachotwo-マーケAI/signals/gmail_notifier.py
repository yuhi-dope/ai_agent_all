"""Gmail APIで杉本に通知メール送信"""

from __future__ import annotations

from outreach.email_sender import send_email
from config import settings


def notify_hot_lead(company: dict, contact: dict, meeting: dict | None = None) -> None:
    """HOTリード通知（商談確定時）"""
    meeting_info = ""
    if meeting:
        meeting_info = f"""
        <tr><td>日時</td><td>{meeting.get('date', '')}</td></tr>
        <tr><td>Google Meet</td><td><a href="{meeting.get('url', '')}">{meeting.get('url', '')}</a></td></tr>
        """

    html = f"""
    <h2>🔥【商談確定】{company.get('name', '')} {contact.get('name', '')}様</h2>
    <table border="1" cellpadding="8" style="border-collapse:collapse;">
        <tr><td>企業名</td><td>{company.get('name', '')}</td></tr>
        <tr><td>担当者</td><td>{contact.get('name', '')}</td></tr>
        <tr><td>電話番号</td><td>{contact.get('phone', '')}</td></tr>
        {meeting_info}
        <tr><td>業種</td><td>{company.get('industry', '')}</td></tr>
        <tr><td>従業員数</td><td>{company.get('employee_count', '不明')}名</td></tr>
        <tr><td>代表者</td><td>{company.get('representative', '')}</td></tr>
    </table>
    <h3>推定ニーズ</h3>
    <ul>
        {''.join(f"<li>{p}</li>" for p in company.get('pain_points', []))}
    </ul>
    """

    subject = f"🔥【商談確定】{company.get('name', '')} {contact.get('name', '')}様"
    if meeting:
        subject += f" — {meeting.get('date', '')}"

    send_email(to=settings.sender_email, subject=subject, body_html=html)


def notify_warm_signal(company: dict, signal_type: str) -> None:
    """WARMシグナル通知"""
    labels = {"doc_download": "📄 資料DL", "lp_view_30s": "👀 LP30秒以上閲覧"}
    label = labels.get(signal_type, signal_type)

    html = f"""
    <h2>{label} — {company.get('name', '')}</h2>
    <p>業種: {company.get('industry', '')}</p>
    <p>3日後にフォローメールを自動送信予定です。</p>
    """

    send_email(
        to=settings.sender_email,
        subject=f"{label} — {company.get('name', '')}",
        body_html=html,
    )

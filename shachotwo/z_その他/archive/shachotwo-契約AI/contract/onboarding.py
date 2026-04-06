"""オンボーディング — ウェルカムメール & ヒアリング日程調整"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from jinja2 import Environment, FileSystemLoader

from config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/calendar"]


def _get_gmail():
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES, subject=settings.sender_email)
    return build("gmail", "v1", credentials=creds)


def _send_email(to: str, subject: str, body_html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.sender_name} <{settings.sender_email}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _get_gmail().users().messages().send(userId="me", body={"raw": raw}).execute()


async def send_welcome_email(contract: dict, account_info: dict) -> None:
    """ウェルカムメール送信"""
    env = Environment(loader=FileSystemLoader("contract/templates/emails"))
    template = env.get_template("welcome.html")
    html = template.render(
        company_name=contract.get("company_name", ""),
        contact_name=contract.get("contact_name", ""),
        login_url=f"{settings.app_base_url}/login",
        slots=[],  # TODO: Calendar APIで空き枠取得
    )
    _send_email(
        to=account_info.get("email", ""),
        subject=f"シャチョツーへようこそ — {contract.get('company_name', '')}様",
        body_html=html,
    )


async def notify_owner_contract_complete(contract: dict, company_data: dict) -> None:
    """杉本にGmail通知"""
    html = f"""
    <h2>🎉【契約完了】{company_data.get('name', '')} {contract.get('contact_name', '')}様 — {contract.get('plan', '')} ¥{contract.get('monthly_amount', 0):,}/月</h2>
    <table border="1" cellpadding="8" style="border-collapse:collapse;">
        <tr><td>企業名</td><td>{company_data.get('name', '')}</td></tr>
        <tr><td>担当者</td><td>{contract.get('contact_name', '')}</td></tr>
        <tr><td>プラン</td><td>{contract.get('plan', '')}</td></tr>
        <tr><td>月額</td><td>¥{contract.get('monthly_amount', 0):,}</td></tr>
        <tr><td>決済方法</td><td>{contract.get('payment_method', '')}</td></tr>
        <tr><td>業種</td><td>{company_data.get('industry', '')}</td></tr>
        <tr><td>従業員数</td><td>{company_data.get('employee_count', '不明')}名</td></tr>
    </table>
    """
    _send_email(
        to=settings.sender_email,
        subject=f"🎉【契約完了】{company_data.get('name', '')} — {contract.get('plan', '')} ¥{contract.get('monthly_amount', 0):,}/月",
        body_html=html,
    )

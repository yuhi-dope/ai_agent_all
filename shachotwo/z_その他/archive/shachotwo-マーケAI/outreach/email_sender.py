"""Gmail APIでメール送信（フォールバック）"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _get_service():
    creds = Credentials.from_service_account_file(
        settings.google_credentials_path,
        scopes=SCOPES,
        subject=settings.sender_email,
    )
    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body_html: str) -> bool:
    """HTMLメールを送信"""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.sender_name} <{settings.sender_email}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = _get_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return True


def send_followup(to: str, subject: str, body_html: str) -> bool:
    """フォローメール送信（email_senderと同じだが意味的に分離）"""
    return send_email(to, subject, body_html)

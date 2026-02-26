"""
run 失敗・予算超過時にメールアラートを送信する（Resend HTTP API）。
RESEND_API_KEY 未設定時は何もしない。
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"
_FROM_ADDRESS = "Develop Agent <noreply@resend.dev>"


def _get_api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "").strip()


def _get_recipients() -> list[str]:
    raw = os.environ.get("DEVELOPER_EMAILS", "").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split(",") if e.strip()]


def send_alert(
    subject: str,
    html_body: str,
    run_id: str = "",
    extra_recipients: Optional[list[str]] = None,
) -> None:
    """
    メールアラートを送信する。
    RESEND_API_KEY 未設定時は何もしない。送信失敗でも例外は投げない。
    BackgroundTasks から呼ぶことを想定。
    """
    api_key = _get_api_key()
    if not api_key:
        return

    recipients = _get_recipients()
    if extra_recipients:
        recipients.extend(extra_recipients)
    if not recipients:
        logger.warning("No recipients for alert (DEVELOPER_EMAILS empty)")
        return

    payload = {
        "from": _FROM_ADDRESS,
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                _RESEND_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            logger.warning(
                "Resend API error %d: %s", resp.status_code, resp.text[:200]
            )
        else:
            logger.info(
                "Alert email sent: subject=%r run_id=%s to=%s",
                subject,
                run_id,
                recipients,
            )
    except Exception as e:
        logger.warning("Failed to send alert email: %s", e)


def alert_run_failed(run_id: str, error_detail: str = "") -> None:
    subject = f"[Develop Agent] Run failed: {run_id}"
    html_body = (
        "<h2>Run Failed</h2>"
        f"<p><strong>Run ID:</strong> {run_id}</p>"
        f"<p><strong>Error:</strong> {error_detail or '(no detail)'}</p>"
        "<p>Check the dashboard for more information.</p>"
    )
    send_alert(subject, html_body, run_id=run_id)


def alert_budget_exceeded(run_id: str, cost_usd: float) -> None:
    subject = f"[Develop Agent] Budget exceeded: {run_id}"
    html_body = (
        "<h2>Budget Exceeded</h2>"
        f"<p><strong>Run ID:</strong> {run_id}</p>"
        f"<p><strong>Estimated Cost:</strong> ${cost_usd:.4f}</p>"
        "<p>The run has exceeded the per-task budget limit.</p>"
    )
    send_alert(subject, html_body, run_id=run_id)

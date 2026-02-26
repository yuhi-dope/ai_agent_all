"""
Slack Events API ハンドラー + メッセージ投稿。
Notion Webhook (server/main.py L490-506) と同じ HMAC 署名検証パターンに従う。
BackgroundTasks で非同期実行し、3秒以内に 200 を返す。
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx
from fastapi import HTTPException, Request

from server import oauth_store
from server.channel_adapter import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


# --- Slack 署名検証 ---


def verify_slack_signature(
    raw_body: bytes, timestamp: str, signature: str,
    signing_secret: str | None = None,
) -> bool:
    """
    Slack のリクエスト署名を検証する。
    signing_secret 指定時はそれを使用、None なら環境変数。
    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    secret = signing_secret or ""
    if not secret or not timestamp or not signature:
        return False
    # リプレイ攻撃防止（5分ウィンドウ）
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    sig_basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- Slack Web API ヘルパー ---


def _get_bot_token(tenant_id: str | None = None) -> str:
    """Bot トークンを取得する。tenant_id 指定時はテナント別、なければデフォルト。"""
    if tenant_id:
        stored = oauth_store.get_token("slack", tenant_id=tenant_id)
        if stored and stored.get("access_token"):
            return stored["access_token"]
    stored = oauth_store.get_token("slack")
    if stored and stored.get("access_token"):
        return stored["access_token"]
    return ""


def post_message(
    channel: str, text: str, thread_ts: Optional[str] = None,
    tenant_id: str | None = None,
) -> dict:
    """Slack の chat.postMessage で投稿する。"""
    token = _get_bot_token(tenant_id=tenant_id)
    if not token:
        logger.warning("No Slack bot token available")
        return {}
    payload: dict = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Slack API error: %s", data.get("error"))
        return data
    except Exception as e:
        logger.warning("Failed to post Slack message: %s", e)
        return {}


# --- チャンネルアダプタ ---


class SlackAdapter(ChannelAdapter):
    """Slack Events API アダプタ。"""

    def __init__(self, config: dict | None = None, tenant_id: str | None = None):
        self.config = config or {}
        self.tenant_id = tenant_id

    @property
    def provider_name(self) -> str:
        return "slack"

    async def parse_webhook(self, request: Request) -> Optional[ChannelMessage]:
        raw_body = await request.body()
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")

        payload = json.loads(raw_body.decode("utf-8"))

        # URL Verification challenge は呼び出し元で処理するため None を返す
        if payload.get("type") == "url_verification":
            return None

        signing_secret = self.config.get("signing_secret")
        if not verify_slack_signature(raw_body, timestamp, signature, signing_secret=signing_secret):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")

        event = payload.get("event") or {}

        # Bot 自身のメッセージを無視（無限ループ防止）
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return None

        if event.get("type") != "message":
            return None

        text = (event.get("text") or "").strip()
        if not text:
            return None

        return ChannelMessage(
            source="slack",
            requirement=text,
            sender_id=event.get("user", ""),
            reply_to={
                "channel": event.get("channel", ""),
                "thread_ts": event.get("ts", ""),
            },
            raw_payload=payload,
        )

    async def send_progress(self, reply_to: dict, message: str) -> None:
        post_message(
            channel=reply_to.get("channel", ""),
            text=message,
            thread_ts=reply_to.get("thread_ts"),
            tenant_id=self.tenant_id,
        )

    async def send_result(
        self, reply_to: dict, run_id: str, status: str, detail: str = ""
    ) -> None:
        text = f"*Run completed*\n- Run ID: `{run_id}`\n- Status: `{status}`"
        if detail:
            text += f"\n- Detail: {detail[:500]}"
        post_message(
            channel=reply_to.get("channel", ""),
            text=text,
            thread_ts=reply_to.get("thread_ts"),
            tenant_id=self.tenant_id,
        )

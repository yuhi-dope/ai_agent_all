"""
Chatwork Webhook ハンドラー + メッセージ投稿。
Chatwork は OAuth ではなく API トークン認証。
"""

import logging
import re
from typing import Optional

import httpx
from fastapi import HTTPException, Request

from server.channel_adapter import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)

_CHATWORK_API_BASE = "https://api.chatwork.com/v2"


def _get_api_token(config: dict | None = None) -> str:
    if config and config.get("api_token"):
        return config["api_token"]
    return ""


def _get_webhook_token(config: dict | None = None) -> str:
    if config and config.get("webhook_token"):
        return config["webhook_token"]
    return ""


def post_message(room_id: str, body: str, config: dict | None = None) -> dict:
    """Chatwork のルームにメッセージを投稿する。"""
    token = _get_api_token(config)
    if not token:
        logger.warning("CHATWORK_API_TOKEN not set")
        return {}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{_CHATWORK_API_BASE}/rooms/{room_id}/messages",
                data={"body": body},
                headers={"X-ChatWorkToken": token},
            )
        if resp.status_code >= 400:
            logger.warning(
                "Chatwork API error %d: %s", resp.status_code, resp.text[:200]
            )
            return {}
        return resp.json()
    except Exception as e:
        logger.warning("Failed to post Chatwork message: %s", e)
        return {}


class ChatworkAdapter(ChannelAdapter):
    """Chatwork Webhook アダプタ。"""

    def __init__(self, config: dict | None = None, tenant_id: str | None = None):
        self.config = config or {}
        self.tenant_id = tenant_id

    @property
    def provider_name(self) -> str:
        return "chatwork"

    async def parse_webhook(self, request: Request) -> Optional[ChannelMessage]:
        # Webhook トークン検証
        webhook_token = _get_webhook_token(self.config)
        header_token = request.headers.get("X-ChatWorkWebhookSignature", "")

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        if webhook_token and header_token != webhook_token:
            raise HTTPException(
                status_code=401, detail="Invalid Chatwork webhook token"
            )

        webhook_event = payload.get("webhook_event") or {}
        message_id = str(webhook_event.get("message_id", ""))
        body = (webhook_event.get("body") or "").strip()
        room_id = str(webhook_event.get("room_id", ""))
        account = webhook_event.get("account") or {}
        account_id = str(account.get("account_id", ""))

        if not body or not room_id:
            return None

        # メンションフィルタ: bot 宛のメッセージのみ処理
        bot_id = self.config.get("bot_account_id", "")
        if bot_id and f"[To:{bot_id}]" not in body:
            return None

        # [To:xxx] タグを除去して要件テキストを抽出
        clean_body = re.sub(r"\[To:\d+\]\s*", "", body).strip()
        if not clean_body:
            return None

        return ChannelMessage(
            source="chatwork",
            requirement=clean_body,
            sender_id=account_id,
            reply_to={"room_id": room_id, "message_id": message_id},
            raw_payload=payload,
        )

    async def send_progress(self, reply_to: dict, message: str) -> None:
        room_id = reply_to.get("room_id", "")
        if room_id:
            post_message(room_id, message, config=self.config)

    async def send_result(
        self, reply_to: dict, run_id: str, status: str, detail: str = ""
    ) -> None:
        room_id = reply_to.get("room_id", "")
        if not room_id:
            return
        body = f"[info][title]Run Complete[/title]Run ID: {run_id}\nStatus: {status}"
        if detail:
            body += f"\nDetail: {detail[:500]}"
        body += "[/info]"
        post_message(room_id, body, config=self.config)

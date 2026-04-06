"""Google Workspace Push 通知受信エンドポイント — Gmail / Calendar。

Gmail: Google Pub/Sub → POST /webhooks/gmail-push
Calendar: Calendar Watch API → POST /webhooks/calendar-push
"""
import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class GWSWebhookResponse(BaseModel):
    received: bool = True
    message: str = "ok"


# ---------------------------------------------------------------------------
# Gmail Push Notification (via Pub/Sub)
# ---------------------------------------------------------------------------


@router.post(
    "/webhooks/gmail-push",
    response_model=GWSWebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def gmail_push_notification(request: Request):
    """Google Pub/Sub から Gmail Watch Push 通知を受信する。

    Pub/Sub ペイロード形式:
    {
        "message": {
            "data": base64("{"emailAddress": "user@example.com", "historyId": "12345"}"),
            "messageId": "...",
            "publishTime": "..."
        },
        "subscription": "projects/.../subscriptions/..."
    }

    処理:
    1. Pub/Sub メッセージをデコード
    2. emailAddress → company_id マッピング（watch_channels テーブル）
    3. watch_manager.handle_gmail_push() に委譲
    4. 200 OK を即返し（Pub/Sub 再送防止）
    """
    try:
        body = await request.json()
        message = body.get("message", {})
        data_b64 = message.get("data", "")

        if not data_b64:
            return GWSWebhookResponse(message="empty data")

        decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        email_address = decoded.get("emailAddress", "")
        history_id = decoded.get("historyId", "")

        if not email_address or not history_id:
            logger.warning("gmail-push: emailAddress or historyId missing")
            return GWSWebhookResponse(message="missing fields")

        logger.info(
            "gmail-push: received email=%s historyId=%s",
            email_address,
            history_id,
        )

        # company_id の解決と処理を非同期で実行
        import asyncio
        asyncio.create_task(
            _process_gmail_push(email_address, history_id)
        )

        return GWSWebhookResponse(message="accepted")

    except Exception as e:
        logger.error("gmail-push webhook error: %s", e)
        # Pub/Sub は 2xx 以外でリトライするため、必ず 200 を返す
        return GWSWebhookResponse(received=False, message="error")


async def _process_gmail_push(email_address: str, history_id: str) -> None:
    """Gmail Push 通知の非同期処理。"""
    try:
        from workers.gws.watch_manager import handle_gmail_push
        await handle_gmail_push(email_address, history_id)
    except Exception as e:
        logger.error("gmail-push processing failed: %s", e)


# ---------------------------------------------------------------------------
# Calendar Push Notification
# ---------------------------------------------------------------------------


@router.post(
    "/webhooks/calendar-push",
    response_model=GWSWebhookResponse,
    status_code=status.HTTP_200_OK,
)
async def calendar_push_notification(request: Request):
    """Google Calendar Watch Push 通知を受信する。

    ヘッダ:
        X-Goog-Channel-ID: チャネルID（watch_channels.channel_id と突合）
        X-Goog-Resource-State: "sync" | "exists" | "not_exists"
        X-Goog-Resource-ID: リソースID
        X-Goog-Resource-URI: リソースURI
        X-Goog-Channel-Expiration: 有効期限

    処理:
    1. X-Goog-Channel-ID → watch_channels テーブルで company_id 特定
    2. resource_state == "sync": 初回同期確認（無視）
    3. resource_state == "exists": イベント変更検知 → watch_manager に委譲
    4. 200 OK を即返し
    """
    try:
        channel_id = request.headers.get("X-Goog-Channel-ID", "")
        resource_state = request.headers.get("X-Goog-Resource-State", "")
        resource_id = request.headers.get("X-Goog-Resource-ID", "")

        if not channel_id:
            return GWSWebhookResponse(message="no channel_id")

        # 初回同期確認（sync）は無視
        if resource_state == "sync":
            logger.debug("calendar-push: sync notification for channel=%s", channel_id)
            return GWSWebhookResponse(message="sync acknowledged")

        if resource_state != "exists":
            logger.debug(
                "calendar-push: ignoring state=%s for channel=%s",
                resource_state,
                channel_id,
            )
            return GWSWebhookResponse(message="ignored")

        logger.info(
            "calendar-push: received channel=%s resource=%s state=%s",
            channel_id,
            resource_id,
            resource_state,
        )

        # 非同期で処理
        import asyncio
        asyncio.create_task(
            _process_calendar_push(channel_id, resource_id)
        )

        return GWSWebhookResponse(message="accepted")

    except Exception as e:
        logger.error("calendar-push webhook error: %s", e)
        return GWSWebhookResponse(received=False, message="error")


async def _process_calendar_push(channel_id: str, resource_id: str) -> None:
    """Calendar Push 通知の非同期処理。"""
    try:
        from workers.gws.watch_manager import handle_calendar_push
        await handle_calendar_push(channel_id, resource_id)
    except Exception as e:
        logger.error("calendar-push processing failed: %s", e)

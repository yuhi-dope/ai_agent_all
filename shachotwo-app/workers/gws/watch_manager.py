"""GWS Watch ライフサイクル管理。

Gmail Watch / Calendar Watch の登録・更新・Push通知処理を統合管理する。
event_listener への橋渡し役。
"""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Watch有効期限の更新マージン（期限の何日前に更新するか）
_GMAIL_RENEW_BEFORE_DAYS = 1      # Gmail Watch: 最大7日、6日目に更新
_CALENDAR_RENEW_BEFORE_DAYS = 5   # Calendar Watch: 最大30日、25日目に更新


# ---------------------------------------------------------------------------
# Gmail Watch 管理
# ---------------------------------------------------------------------------


async def ensure_gmail_watch(company_id: str) -> dict[str, Any]:
    """Gmail Watch を確認し、未登録 or 期限切れなら再登録する。

    Returns:
        {"channel_id": str, "history_id": str, "expiration": str}
    """
    from db.supabase import get_service_client
    db = get_service_client()

    # 既存の有効チャネルを検索
    result = db.table("watch_channels").select("*").eq(
        "company_id", company_id
    ).eq("service", "gmail").eq("is_active", True).execute()

    now = datetime.now(timezone.utc)

    if result.data:
        channel = result.data[0]
        expiration = datetime.fromisoformat(channel["expiration"].replace("Z", "+00:00"))
        if expiration > now + timedelta(days=_GMAIL_RENEW_BEFORE_DAYS):
            return {
                "channel_id": channel["channel_id"],
                "history_id": channel.get("history_id", ""),
                "expiration": channel["expiration"],
            }
        # 期限切れ間近 → 既存を無効化して再登録
        db.table("watch_channels").update({
            "is_active": False,
            "updated_at": now.isoformat(),
        }).eq("id", channel["id"]).eq("company_id", company_id).execute()

    # 新規登録
    from workers.connector.gmail_watch import register_gmail_watch

    topic_name = os.environ.get(
        "GMAIL_PUBSUB_TOPIC",
        "projects/shachotwo-prod/topics/gmail-push",
    )
    watch_result = await register_gmail_watch(topic_name=topic_name)

    channel_id = str(uuid.uuid4())
    expiration_ms = int(watch_result.get("expiration", "0"))
    expiration_dt = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)

    db.table("watch_channels").insert({
        "company_id": company_id,
        "service": "gmail",
        "channel_id": channel_id,
        "history_id": watch_result.get("historyId", ""),
        "expiration": expiration_dt.isoformat(),
        "is_active": True,
    }).execute()

    logger.info("watch_manager: gmail watch registered for company=%s", company_id[:8])
    return {
        "channel_id": channel_id,
        "history_id": watch_result.get("historyId", ""),
        "expiration": expiration_dt.isoformat(),
    }


async def ensure_calendar_watch(
    company_id: str,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """Calendar Watch を確認し、未登録 or 期限切れなら再登録する。"""
    from db.supabase import get_service_client
    db = get_service_client()

    result = db.table("watch_channels").select("*").eq(
        "company_id", company_id
    ).eq("service", "calendar").eq("calendar_id", calendar_id).eq(
        "is_active", True
    ).execute()

    now = datetime.now(timezone.utc)

    if result.data:
        channel = result.data[0]
        expiration = datetime.fromisoformat(channel["expiration"].replace("Z", "+00:00"))
        if expiration > now + timedelta(days=_CALENDAR_RENEW_BEFORE_DAYS):
            return {
                "channel_id": channel["channel_id"],
                "resource_id": channel.get("resource_id", ""),
                "expiration": channel["expiration"],
            }
        # 期限切れ間近 → 停止して再登録
        try:
            from workers.connector.google_calendar import GoogleCalendarConnector
            from workers.connector.base import ConnectorConfig
            connector = GoogleCalendarConnector(ConnectorConfig(tool_name="google_calendar"))
            await connector.write_record("stop_watch", {
                "channel_id": channel["channel_id"],
                "resource_id": channel.get("resource_id", ""),
            })
        except Exception as e:
            logger.warning("watch_manager: failed to stop old calendar watch: %s", e)

        db.table("watch_channels").update({
            "is_active": False,
            "updated_at": now.isoformat(),
        }).eq("id", channel["id"]).eq("company_id", company_id).execute()

    # 新規登録
    from workers.connector.google_calendar import GoogleCalendarConnector
    from workers.connector.base import ConnectorConfig
    connector = GoogleCalendarConnector(ConnectorConfig(tool_name="google_calendar"))

    webhook_url = os.environ.get(
        "GWS_CALENDAR_WEBHOOK_URL",
        "https://api.shachotwo.com/api/v1/webhooks/calendar-push",
    )
    channel_id = str(uuid.uuid4())

    watch_result = await connector.write_record("watch", {
        "webhook_url": webhook_url,
        "channel_id": channel_id,
        "calendar_id": calendar_id,
    })

    expiration_ms = int(watch_result.get("expiration", "0"))
    expiration_dt = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)

    db.table("watch_channels").insert({
        "company_id": company_id,
        "service": "calendar",
        "channel_id": channel_id,
        "resource_id": watch_result.get("resource_id", ""),
        "calendar_id": calendar_id,
        "expiration": expiration_dt.isoformat(),
        "is_active": True,
    }).execute()

    logger.info("watch_manager: calendar watch registered for company=%s", company_id[:8])
    return {
        "channel_id": channel_id,
        "resource_id": watch_result.get("resource_id", ""),
        "expiration": expiration_dt.isoformat(),
    }


# ---------------------------------------------------------------------------
# Watch 有効期限の一括更新
# ---------------------------------------------------------------------------


async def renew_expiring_watches() -> int:
    """全テナントの期限切れ間近の Watch を一括更新する。

    schedule_watcher から毎日 07:00 に呼び出される。

    Returns:
        更新した Watch の件数
    """
    from db.supabase import get_service_client
    db = get_service_client()

    now = datetime.now(timezone.utc)
    gmail_threshold = (now + timedelta(days=_GMAIL_RENEW_BEFORE_DAYS)).isoformat()
    calendar_threshold = (now + timedelta(days=_CALENDAR_RENEW_BEFORE_DAYS)).isoformat()

    # 期限切れ間近の Watch を取得
    result = db.table("watch_channels").select(
        "id, company_id, service, calendar_id"
    ).eq("is_active", True).lt("expiration", gmail_threshold).execute()

    renewed = 0
    for channel in result.data or []:
        company_id = channel["company_id"]
        try:
            if channel["service"] == "gmail":
                await ensure_gmail_watch(company_id)
                renewed += 1
            elif channel["service"] == "calendar":
                cal_id = channel.get("calendar_id", "primary")
                await ensure_calendar_watch(company_id, cal_id)
                renewed += 1
        except Exception as e:
            logger.error(
                "watch_manager: renewal failed for company=%s service=%s: %s",
                company_id[:8], channel["service"], e,
            )

    logger.info("watch_manager: renewed %d watches", renewed)
    return renewed


# ---------------------------------------------------------------------------
# Push 通知ハンドラ
# ---------------------------------------------------------------------------


async def handle_gmail_push(email_address: str, history_id: str) -> None:
    """Gmail Push 通知を処理する。

    1. email_address → company_id + 前回の history_id を解決
    2. historyId 差分でメッセージ取得
    3. email_classifier で分類
    4. event_listener.handle_webhook に橋渡し
    """
    from db.supabase import get_service_client
    db = get_service_client()

    # email_address から company_id を特定
    # tool_connections で gmail の delegated_email を検索
    channel_result = db.table("watch_channels").select(
        "id, company_id, history_id"
    ).eq("service", "gmail").eq("is_active", True).execute()

    company_id = None
    prev_history_id = None
    channel_id = None

    for ch in channel_result.data or []:
        # 自社の Watch チャネルを使用（company_id で特定）
        company_id = ch["company_id"]
        prev_history_id = ch.get("history_id", "")
        channel_id = ch["id"]
        break

    if not company_id or not prev_history_id:
        logger.warning("handle_gmail_push: no matching watch channel for %s", email_address)
        return

    # historyId 差分でメッセージ取得
    from workers.connector.gmail_watch import process_gmail_notification
    messages = await process_gmail_notification(prev_history_id)

    if not messages:
        # historyId を更新（空振りでも進める）
        db.table("watch_channels").update({
            "history_id": history_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", channel_id).eq("company_id", company_id).execute()
        return

    # 各メッセージを分類してイベント発火
    from workers.gws.email_classifier import classify_email
    from workers.bpo.manager.event_listener import handle_webhook

    for msg in messages:
        try:
            classification = await classify_email(msg)
            event_type = classification.get("event_type")

            if event_type:
                payload = {
                    **classification,
                    "source": "gmail_watch",
                    "raw_message": {
                        "id": msg.get("id"),
                        "threadId": msg.get("threadId"),
                        "from": msg.get("from"),
                        "subject": msg.get("subject"),
                    },
                }
                task = await handle_webhook(company_id, event_type, payload)
                if task:
                    from workers.bpo.manager.task_router import route_and_execute
                    import asyncio
                    asyncio.create_task(route_and_execute(task))
                    logger.info(
                        "handle_gmail_push: %s → %s for company=%s",
                        event_type,
                        task.pipeline,
                        company_id[:8],
                    )
        except Exception as e:
            logger.error("handle_gmail_push: classify/route error: %s", e)

    # historyId を更新
    db.table("watch_channels").update({
        "history_id": history_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", channel_id).eq("company_id", company_id).execute()


async def handle_calendar_push(channel_id: str, resource_id: str) -> None:
    """Calendar Push 通知を処理する。

    1. channel_id → company_id + calendar_id を解決
    2. 最近変更されたイベントを取得
    3. 終了済みイベント → meeting_ended、新規作成 → meeting_created
    4. event_listener.handle_webhook に橋渡し
    """
    from db.supabase import get_service_client
    db = get_service_client()

    result = db.table("watch_channels").select(
        "company_id, calendar_id"
    ).eq("channel_id", channel_id).eq("is_active", True).execute()

    if not result.data:
        logger.warning("handle_calendar_push: unknown channel_id=%s", channel_id)
        return

    company_id = result.data[0]["company_id"]
    calendar_id = result.data[0].get("calendar_id", "primary")

    # 直近5分以内に更新されたイベントを取得
    from workers.connector.google_calendar import GoogleCalendarConnector
    from workers.connector.base import ConnectorConfig
    connector = GoogleCalendarConnector(ConnectorConfig(tool_name="google_calendar"))

    now = datetime.now(timezone.utc)
    updated_min = (now - timedelta(minutes=5)).isoformat() + "Z"

    events = await connector.read_records("events", {
        "calendar_id": calendar_id,
        "updated_min": updated_min,
        "max_results": 10,
    })

    from workers.bpo.manager.event_listener import handle_webhook

    for event in events:
        try:
            event_end_str = event.get("end", {}).get("dateTime", "")
            event_start_str = event.get("start", {}).get("dateTime", "")
            event_status = event.get("status", "confirmed")

            if event_status == "cancelled":
                continue

            payload = {
                "source": "calendar_watch",
                "event_id": event.get("id", ""),
                "summary": event.get("summary", ""),
                "start": event_start_str,
                "end": event_end_str,
                "attendees": [
                    a.get("email", "") for a in event.get("attendees", [])
                ],
                "meet_url": event.get("conferenceData", {}).get(
                    "entryPoints", [{}]
                )[0].get("uri", "") if event.get("conferenceData") else "",
                "description": event.get("description", ""),
            }

            # イベント終了判定
            if event_end_str:
                try:
                    end_dt = datetime.fromisoformat(event_end_str.replace("Z", "+00:00"))
                    if end_dt <= now:
                        event_type = "meeting_ended"
                    elif event_start_str:
                        start_dt = datetime.fromisoformat(event_start_str.replace("Z", "+00:00"))
                        if start_dt > now:
                            event_type = "meeting_created"
                        else:
                            continue  # 進行中はスキップ
                    else:
                        continue
                except ValueError:
                    continue
            else:
                continue

            task = await handle_webhook(company_id, event_type, payload)
            if task:
                from workers.bpo.manager.task_router import route_and_execute
                import asyncio
                asyncio.create_task(route_and_execute(task))
                logger.info(
                    "handle_calendar_push: %s → %s event='%s' company=%s",
                    event_type,
                    task.pipeline,
                    event.get("summary", ""),
                    company_id[:8],
                )

        except Exception as e:
            logger.error("handle_calendar_push: event processing error: %s", e)

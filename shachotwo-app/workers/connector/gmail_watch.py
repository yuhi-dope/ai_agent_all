"""Gmail Watch API 管理モジュール。

GmailConnector（送受信）とは責務分離。
Watch の登録/停止/historyId差分取得を担当する。

Google Pub/Sub 経由で Push 通知を受ける前提:
  トピック: projects/{project_id}/topics/gmail-push
  サービスアカウントに Pub/Sub Publisher 権限が必要。
"""
import base64
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _get_gmail_service(delegated_email: str = ""):
    """Gmail API サービスオブジェクトを取得（Watch専用）。"""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    email = delegated_email or os.environ.get("GMAIL_DELEGATED_EMAIL", "")
    if email:
        creds = creds.with_subject(email)

    return build("gmail", "v1", credentials=creds)


async def register_gmail_watch(
    topic_name: str,
    label_ids: list[str] | None = None,
    delegated_email: str = "",
) -> dict[str, Any]:
    """Gmail Watch API で Pub/Sub Push 通知を登録する。

    Args:
        topic_name: Pub/Sub トピック名
            例: "projects/shachotwo-prod/topics/gmail-push"
        label_ids: 監視対象のラベルID (デフォルト: ["INBOX"])
        delegated_email: DWD 対象メールアドレス

    Returns:
        {"historyId": str, "expiration": str}
        expiration は UNIX ミリ秒（最大7日後）
    """
    service = _get_gmail_service(delegated_email)

    body: dict[str, Any] = {
        "topicName": topic_name,
        "labelIds": label_ids or ["INBOX"],
    }

    result = service.users().watch(userId="me", body=body).execute()
    logger.info(
        "gmail_watch: registered watch historyId=%s expiration=%s",
        result.get("historyId"),
        result.get("expiration"),
    )
    return {
        "historyId": str(result.get("historyId", "")),
        "expiration": str(result.get("expiration", "")),
    }


async def stop_gmail_watch(
    delegated_email: str = "",
) -> None:
    """Gmail Watch を停止する。

    Note: Gmail API の watch 停止は users.stop() で行う。
    channel_id/resource_id は不要（Gmail 固有の仕様）。
    """
    service = _get_gmail_service(delegated_email)
    service.users().stop(userId="me").execute()
    logger.info("gmail_watch: stopped watch")


async def process_gmail_notification(
    history_id: str,
    delegated_email: str = "",
) -> list[dict[str, Any]]:
    """historyId 差分から新着メッセージを取得する。

    Pub/Sub 通知に含まれる historyId を起点に、
    それ以降に追加されたメッセージの詳細を返す。

    Args:
        history_id: 前回処理した historyId
        delegated_email: DWD 対象メールアドレス

    Returns:
        list[dict] — 各要素:
            {id, threadId, from, to, subject, date, snippet, body_text}
    """
    service = _get_gmail_service(delegated_email)

    try:
        history_result = service.users().history().list(
            userId="me",
            startHistoryId=history_id,
            historyTypes=["messageAdded"],
            labelId="INBOX",
        ).execute()
    except Exception as e:
        logger.warning("gmail_watch: history.list failed (historyId=%s): %s", history_id, e)
        return []

    histories = history_result.get("history", [])
    message_ids: set[str] = set()

    for h in histories:
        for added in h.get("messagesAdded", []):
            msg = added.get("message", {})
            msg_id = msg.get("id", "")
            if msg_id:
                message_ids.add(msg_id)

    messages: list[dict[str, Any]] = []
    for msg_id in message_ids:
        try:
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="full",
            ).execute()
            parsed = _parse_message(msg)
            messages.append(parsed)
        except Exception as e:
            logger.warning("gmail_watch: message.get failed id=%s: %s", msg_id, e)

    logger.info("gmail_watch: processed %d new messages from historyId=%s", len(messages), history_id)
    return messages


def _parse_message(raw_msg: dict) -> dict[str, Any]:
    """Gmail API メッセージをフラットなdictに変換する。"""
    headers = {
        h["name"].lower(): h["value"]
        for h in raw_msg.get("payload", {}).get("headers", [])
    }
    body_text = _extract_text_body(raw_msg.get("payload", {}))
    return {
        "id": raw_msg.get("id", ""),
        "threadId": raw_msg.get("threadId", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": raw_msg.get("snippet", ""),
        "body_text": body_text,
        "labels": raw_msg.get("labelIds", []),
    }


def _extract_text_body(payload: dict) -> str:
    """Gmail payload からテキストボディを抽出する。"""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        result = _extract_text_body(part)
        if result:
            return result

    return ""

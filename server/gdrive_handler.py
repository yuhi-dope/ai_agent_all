"""
Google Drive 統合: OAuth トークンリフレッシュ、フォルダポーリング、Docs テキスト取得。
MVP はポーリング方式（Push Notification は 24h 期限再登録が必要なため見送り）。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import Request

from server import oauth_store
from server.channel_adapter import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


# --- トークンリフレッシュ ---


def _refresh_google_token(
    tenant_id: str | None = None,
    client_id_override: str | None = None,
    client_secret_override: str | None = None,
) -> Optional[str]:
    """Google OAuth トークンを自動リフレッシュする。有効なトークンを返す。"""
    tid = tenant_id or "default"
    token_data = oauth_store.get_token("gdrive", tenant_id=tid)
    if not token_data and tid != "default":
        token_data = oauth_store.get_token("gdrive")
    if not token_data:
        return None

    if not oauth_store.is_token_expired(token_data):
        return token_data.get("access_token")

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        logger.warning("Google token expired and no refresh_token available")
        return None

    client_id = client_id_override or ""
    client_secret = client_secret_override or ""
    if not client_id or not client_secret:
        logger.warning("Google token refresh skipped: missing client_id or client_secret")
        return None

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
    except Exception as e:
        logger.warning("Google token refresh request failed: %s", e)
        return None

    if resp.status_code != 200:
        logger.warning("Google token refresh failed: %s", resp.text[:200])
        return None

    data = resp.json()
    expires_in = int(data.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    oauth_store.save_token(
        provider="gdrive",
        access_token=data["access_token"],
        refresh_token=refresh_token,
        expires_at=expires_at,
        scopes=data.get("scope"),
        raw_response=data,
        tenant_id=tid,
    )
    return data["access_token"]


def get_access_token(
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> Optional[str]:
    """有効な Google アクセストークンを取得する（必要に応じてリフレッシュ）。"""
    return _refresh_google_token(
        tenant_id=tenant_id,
        client_id_override=client_id,
        client_secret_override=client_secret,
    )


# --- Google Docs テキスト取得 ---


def fetch_doc_as_text(
    doc_id: str,
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Google Docs をプレーンテキストとしてエクスポートする。"""
    token = get_access_token(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    if not token:
        return ""
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                f"https://www.googleapis.com/drive/v3/files/{doc_id}/export",
                params={"mimeType": "text/plain"},
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.warning("Failed to export doc %s: %d", doc_id, resp.status_code)
            return ""
        return resp.text
    except Exception as e:
        logger.warning("Failed to fetch doc %s: %s", doc_id, e)
        return ""


# --- フォルダポーリング ---


def poll_folder_for_new_docs(
    folder_id: str,
    since_minutes: int = 30,
    tenant_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> list[dict]:
    """
    指定フォルダ内で直近 N 分間に更新された Google Docs を検出する。
    返却: list[{id, name, modifiedTime}]
    """
    token = get_access_token(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    if not token:
        return []

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    ).isoformat()
    query = (
        f"'{folder_id}' in parents"
        f" and modifiedTime > '{cutoff}'"
        f" and mimeType = 'application/vnd.google-apps.document'"
    )

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                "https://www.googleapis.com/drive/v3/files",
                params={
                    "q": query,
                    "fields": "files(id,name,modifiedTime)",
                    "orderBy": "modifiedTime desc",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.warning("Drive API list error: %d", resp.status_code)
            return []
        return resp.json().get("files", [])
    except Exception as e:
        logger.warning("Drive API poll failed: %s", e)
        return []


# --- 結果コメント ---


def add_comment_to_doc(doc_id: str, comment: str) -> None:
    """Google Drive ファイルにコメントを追加する。"""
    token = get_access_token()
    if not token:
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments",
                json={"content": comment},
                params={"fields": "id"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
    except Exception as e:
        logger.warning("Failed to add comment to doc %s: %s", doc_id, e)


# --- チャンネルアダプタ ---


class GDriveAdapter(ChannelAdapter):
    """
    Google Drive アダプタ。
    Slack/Chatwork と異なりポーリング + 手動トリガー方式。
    parse_webhook は POST /webhook/gdrive {doc_id: "..."} を処理する。
    """

    def __init__(self, config: dict | None = None, tenant_id: str | None = None):
        self.config = config or {}
        self.tenant_id = tenant_id

    @property
    def provider_name(self) -> str:
        return "gdrive"

    def _get_token(self) -> Optional[str]:
        return get_access_token(
            tenant_id=self.tenant_id,
            client_id=self.config.get("client_id"),
            client_secret=self.config.get("client_secret"),
        )

    async def parse_webhook(self, request: Request) -> Optional[ChannelMessage]:
        """手動トリガー: {doc_id: "..."} を受け取り Docs の内容を取得する。"""
        try:
            payload = await request.json()
        except Exception:
            return None

        doc_id = (payload.get("doc_id") or "").strip()
        if not doc_id:
            return None

        content = fetch_doc_as_text(doc_id)
        if not content.strip():
            return None

        return ChannelMessage(
            source="gdrive",
            requirement=content,
            sender_id="",
            reply_to={"doc_id": doc_id},
            raw_payload=payload,
        )

    async def send_progress(self, reply_to: dict, message: str) -> None:
        doc_id = reply_to.get("doc_id", "")
        if doc_id:
            add_comment_to_doc(doc_id, f"[Progress] {message}")

    async def send_result(
        self, reply_to: dict, run_id: str, status: str, detail: str = ""
    ) -> None:
        doc_id = reply_to.get("doc_id", "")
        if not doc_id:
            return
        comment = f"[Result] Run ID: {run_id}, Status: {status}"
        if detail:
            comment += f"\n{detail[:500]}"
        add_comment_to_doc(doc_id, comment)

"""GmailConnector — Gmail API コネクタ（Google Workspace）。

SendGrid から Gmail API へ置き換え済み。
- 認証: サービスアカウント（google-api-python-client）
- 送信上限: Google Workspace 2,000件/日（メモリ追跡）
- バースト防止: 送信間隔最低1秒
- 添付ファイル: base64エンコード（Gmail API 仕様）
"""
import asyncio
import base64
import email.mime.base
import email.mime.multipart
import email.mime.text
import logging
import os
import time
from datetime import date, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Google Workspace の1日あたり送信上限
DAILY_SEND_LIMIT = 2000

# 送信間隔（秒）— バースト防止
SEND_INTERVAL_SECONDS = 1.0

# 開封トラッキング用1px透過GIF（base64）
_TRACKING_PIXEL_GIF_B64 = (
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


class GmailConnector(BaseConnector):
    """Gmail API コネクタ（Google Workspace）。

    credentials（tool_connections テーブルから復号して渡す）:
        credentials_path (str): サービスアカウントJSONファイルのパス
            または GOOGLE_CREDENTIALS_PATH 環境変数で代替可
        sender_email (str): 送信元メールアドレス（Workspaceユーザー）
            または SENDER_EMAIL 環境変数で代替可
        delegated_email (str, optional): ドメイン委任対象アドレス。
            指定時はそのユーザーとして送信（DWD: Domain-Wide Delegation）

    read_records の resource:
        "inbox" — 受信メール一覧

    write_record の resource:
        "send" — メール送信
        data キー:
            to (str | list[str]): 宛先
            subject (str): 件名
            body_html (str): HTMLボディ
            attachments (list[dict], optional): 添付ファイルリスト
                各要素: {filename: str, content_b64: str, mime_type: str}
            tracking (bool, optional): 開封トラッキング用ピクセル挿入
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._service = None
        # 日次送信カウンタ（メモリ追跡）
        self._send_count_date: date = date.today()
        self._send_count: int = 0
        # 最後の送信時刻（バースト防止用）
        self._last_send_time: float = 0.0
        self._send_lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # 内部ヘルパー
    # -----------------------------------------------------------------------

    def _get_service(self):
        """Gmail API サービスオブジェクトを取得（遅延初期化）。

        認証情報は credentials_path に指定したサービスアカウントJSONを使用。
        delegated_email が指定されている場合は DWD（ドメイン委任）を適用する。
        """
        if self._service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            creds_path = self.config.credentials.get(
                "credentials_path",
                os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
            )
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

            delegated = self.config.credentials.get(
                "delegated_email",
                os.environ.get("GMAIL_DELEGATED_EMAIL", ""),
            )
            if delegated:
                creds = creds.with_subject(delegated)

            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    @property
    def _sender_email(self) -> str:
        return self.config.credentials.get(
            "sender_email",
            os.environ.get("SENDER_EMAIL", "me"),
        )

    def _reset_count_if_new_day(self) -> None:
        """日付が変わっていたら送信カウンタをリセットする。"""
        today = date.today()
        if today != self._send_count_date:
            self._send_count_date = today
            self._send_count = 0

    def _remaining_quota(self) -> int:
        """本日の残送信可能件数を返す。"""
        self._reset_count_if_new_day()
        return max(0, DAILY_SEND_LIMIT - self._send_count)

    def _build_message(
        self,
        to: str | list[str],
        subject: str,
        body_html: str,
        attachments: list[dict] | None = None,
        tracking_url: str | None = None,
    ) -> str:
        """MIME メッセージを構築して base64url エンコード文字列を返す。

        Args:
            to: 宛先（str または list[str]）
            subject: 件名
            body_html: HTMLボディ
            attachments: 添付ファイルリスト
                [{"filename": str, "content_b64": str, "mime_type": str}, ...]
            tracking_url: 開封トラッキング画像URL（Noneならピクセル挿入なし）

        Returns:
            base64url エンコードされた RFC 2822 メッセージ文字列
        """
        to_list = [to] if isinstance(to, str) else to
        to_str = ", ".join(to_list)

        if attachments:
            msg = MIMEMultipart("mixed")
        else:
            msg = MIMEMultipart("alternative")

        msg["To"] = to_str
        msg["From"] = self._sender_email
        msg["Subject"] = subject

        # トラッキングピクセルの挿入
        if tracking_url:
            pixel_tag = (
                f'<img src="{tracking_url}" width="1" height="1" '
                f'style="display:none;" alt="" />'
            )
            body_html = body_html + pixel_tag

        html_part = MIMEText(body_html, "html", "utf-8")

        if attachments:
            # mixed の場合は alternative を子として入れる
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(html_part)
            msg.attach(alt_part)

            for att in attachments:
                filename = att.get("filename", "attachment")
                content_b64 = att.get("content_b64", "")
                mime_type = att.get("mime_type", "application/octet-stream")

                main_type, sub_type = mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
                content_bytes = base64.b64decode(content_b64)

                part = MIMEApplication(content_bytes, _subtype=sub_type)
                part.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(part)
        else:
            msg.attach(html_part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return raw

    async def _enforce_send_interval(self) -> None:
        """送信間隔（最低1秒）を確保する。バースト防止。"""
        now = time.monotonic()
        elapsed = now - self._last_send_time
        if elapsed < SEND_INTERVAL_SECONDS:
            await asyncio.sleep(SEND_INTERVAL_SECONDS - elapsed)

    # -----------------------------------------------------------------------
    # BaseConnector 抽象メソッド実装
    # -----------------------------------------------------------------------

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Gmail 受信メールを取得する。

        Args:
            resource: "inbox" — 受信メール取得
            filters:
                since (str, optional): ISO8601形式の日時。この日時以降のメールを取得。
                    例: "2026-03-01T00:00:00"
                max_results (int, optional): 最大取得件数（デフォルト50）
                query (str, optional): Gmail検索クエリ（例: "from:example.com"）

        Returns:
            メッセージの list[dict]
            各要素: {id, threadId, from, to, subject, date, snippet, body_html}
        """
        if resource != "inbox":
            logger.warning("GmailConnector.read_records: resource '%s' は未サポートです", resource)
            return []

        return await self.fetch_inbound(
            since=filters.get("since"),
            max_results=filters.get("max_results", 50),
            extra_query=filters.get("query", ""),
        )

    async def write_record(self, resource: str, data: dict) -> dict:
        """Gmail でメールを送信する。

        Args:
            resource: "send" — メール送信
            data:
                to (str | list[str]): 宛先（必須）
                subject (str): 件名（必須）
                body_html (str): HTMLボディ（必須）
                attachments (list[dict], optional): 添付ファイルリスト
                tracking (bool, optional): 開封トラッキング挿入（デフォルト False）

        Returns:
            Gmail API send レスポンス dict
            {id, threadId, labelIds}

        Raises:
            ValueError: 1日の送信上限超過時
            RuntimeError: Gmail API エラー時
        """
        if resource == "send_with_tracking":
            return await self.send_email_with_tracking(
                to=data["to"],
                subject=data["subject"],
                body_html=data["body_html"],
                attachments=data.get("attachments"),
            )

        return await self.send_email(
            to=data["to"],
            subject=data["subject"],
            body_html=data["body_html"],
            attachments=data.get("attachments"),
        )

    async def health_check(self) -> bool:
        """Gmail API 接続確認。

        users.getProfile でプロファイル取得を試みる。

        Returns:
            True: 正常接続, False: 接続失敗
        """
        try:
            service = self._get_service()
            profile = service.users().getProfile(userId="me").execute()
            logger.debug(
                "GmailConnector.health_check: emailAddress=%s",
                profile.get("emailAddress", "unknown"),
            )
            return True
        except Exception as e:
            logger.error("GmailConnector.health_check failed: %s", e)
            return False

    # -----------------------------------------------------------------------
    # Gmail 専用メソッド
    # -----------------------------------------------------------------------

    async def send_email(
        self,
        to: str | list[str],
        subject: str,
        body_html: str,
        attachments: list[dict] | None = None,
    ) -> dict:
        """メールを送信する。

        Args:
            to: 宛先メールアドレス（str または list[str]）
            subject: 件名
            body_html: HTMLボディ
            attachments: 添付ファイルリスト（Noneなら添付なし）
                各要素: {"filename": str, "content_b64": str, "mime_type": str}

        Returns:
            Gmail API send レスポンス dict

        Raises:
            ValueError: 1日の送信上限超過時
            RuntimeError: API エラー時
        """
        async with self._send_lock:
            self._reset_count_if_new_day()
            if self._send_count >= DAILY_SEND_LIMIT:
                raise ValueError(
                    f"1日の送信上限（{DAILY_SEND_LIMIT}件）に達しました。"
                    f"本日の送信数: {self._send_count}"
                )

            await self._enforce_send_interval()

            raw = self._build_message(to, subject, body_html, attachments)
            try:
                service = self._get_service()
                result = service.users().messages().send(
                    userId="me",
                    body={"raw": raw},
                ).execute()
                self._send_count += 1
                self._last_send_time = time.monotonic()
                logger.info(
                    "GmailConnector.send_email: sent to=%s subject='%s' daily_count=%d",
                    to if isinstance(to, str) else ",".join(to),
                    subject,
                    self._send_count,
                )
                return result
            except Exception as e:
                logger.error("GmailConnector.send_email failed: %s", e)
                raise RuntimeError(f"Gmail 送信エラー: {e}") from e

    async def send_email_with_tracking(
        self,
        to: str | list[str],
        subject: str,
        body_html: str,
        attachments: list[dict] | None = None,
        tracking_url: str | None = None,
    ) -> dict:
        """開封トラッキング付きメールを送信する。

        HTMLボディの末尾に1px透過トラッキング画像を挿入する。

        Args:
            to: 宛先メールアドレス
            subject: 件名
            body_html: HTMLボディ（トラッキングピクセルを末尾に自動挿入）
            attachments: 添付ファイルリスト
            tracking_url: カスタムトラッキング画像URL。
                Noneの場合は埋め込みbase64 GIFを使用する。

        Returns:
            Gmail API send レスポンス dict
        """
        if tracking_url is None:
            # データURLとして埋め込み（外部サーバー不要版）
            tracking_url = f"data:image/gif;base64,{_TRACKING_PIXEL_GIF_B64}"

        async with self._send_lock:
            self._reset_count_if_new_day()
            if self._send_count >= DAILY_SEND_LIMIT:
                raise ValueError(
                    f"1日の送信上限（{DAILY_SEND_LIMIT}件）に達しました。"
                    f"本日の送信数: {self._send_count}"
                )

            await self._enforce_send_interval()

            raw = self._build_message(
                to, subject, body_html, attachments, tracking_url=tracking_url
            )
            try:
                service = self._get_service()
                result = service.users().messages().send(
                    userId="me",
                    body={"raw": raw},
                ).execute()
                self._send_count += 1
                self._last_send_time = time.monotonic()
                logger.info(
                    "GmailConnector.send_email_with_tracking: sent to=%s daily_count=%d",
                    to if isinstance(to, str) else ",".join(to),
                    self._send_count,
                )
                return result
            except Exception as e:
                logger.error("GmailConnector.send_email_with_tracking failed: %s", e)
                raise RuntimeError(f"Gmail 送信エラー（トラッキング）: {e}") from e

    async def fetch_inbound(
        self,
        since: str | None = None,
        max_results: int = 50,
        extra_query: str = "",
    ) -> list[dict]:
        """受信メールを取得する。

        Args:
            since: ISO8601形式の日時文字列。この日時以降のメールを取得。
                例: "2026-03-01T00:00:00"
            max_results: 最大取得件数
            extra_query: 追加 Gmail 検索クエリ（例: "from:client@example.com"）

        Returns:
            list[dict] — 各要素:
                {id, threadId, from, to, subject, date, snippet, body_html}
        """
        query_parts: list[str] = []

        if since:
            try:
                dt = datetime.fromisoformat(since)
                epoch = int(dt.timestamp())
                query_parts.append(f"after:{epoch}")
            except ValueError:
                logger.warning("GmailConnector.fetch_inbound: since の日時形式が不正です: %s", since)

        if extra_query:
            query_parts.append(extra_query)

        query = " ".join(query_parts) if query_parts else ""

        try:
            service = self._get_service()
            list_params: dict[str, Any] = {
                "userId": "me",
                "maxResults": max_results,
                "labelIds": ["INBOX"],
            }
            if query:
                list_params["q"] = query

            list_result = service.users().messages().list(**list_params).execute()
            message_refs = list_result.get("messages", [])

            messages: list[dict] = []
            for ref in message_refs:
                try:
                    msg = service.users().messages().get(
                        userId="me",
                        id=ref["id"],
                        format="full",
                    ).execute()
                    parsed = self._parse_message(msg)
                    messages.append(parsed)
                except Exception as e:
                    logger.warning(
                        "GmailConnector.fetch_inbound: メッセージ取得失敗 id=%s: %s",
                        ref.get("id"),
                        e,
                    )

            return messages

        except Exception as e:
            logger.error("GmailConnector.fetch_inbound failed: %s", e)
            raise RuntimeError(f"Gmail 受信取得エラー: {e}") from e

    def _parse_message(self, raw_msg: dict) -> dict:
        """Gmail API のメッセージオブジェクトをフラットな dict に変換する。"""
        headers = {
            h["name"].lower(): h["value"]
            for h in raw_msg.get("payload", {}).get("headers", [])
        }
        body_html = self._extract_html_body(raw_msg.get("payload", {}))
        return {
            "id": raw_msg.get("id", ""),
            "threadId": raw_msg.get("threadId", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": raw_msg.get("snippet", ""),
            "body_html": body_html,
        }

    def _extract_html_body(self, payload: dict) -> str:
        """Gmail payload から HTML ボディを再帰的に抽出する。"""
        mime_type = payload.get("mimeType", "")
        if mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            result = self._extract_html_body(part)
            if result:
                return result

        # フォールバック: text/plain を返す
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            if data:
                text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                return f"<pre>{text}</pre>"

        return ""

    async def get_send_quota(self) -> dict:
        """本日の送信可能残数を返す。

        Returns:
            {
                "daily_limit": 2000,
                "sent_today": int,
                "remaining": int,
                "reset_date": "YYYY-MM-DD",
            }
        """
        self._reset_count_if_new_day()
        return {
            "daily_limit": DAILY_SEND_LIMIT,
            "sent_today": self._send_count,
            "remaining": self._remaining_quota(),
            "reset_date": self._send_count_date.isoformat(),
        }

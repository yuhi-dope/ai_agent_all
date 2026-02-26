"""Google Workspace MCP アダプタ.

Gmail・Google Calendar・Google Drive・Google Sheets をMCP経由で操作する。
Google Workspace は全API公開済み。複数のMCPサーバーが利用可能。

対応ジャンル: productivity（全ジャンル横断でドキュメント・スケジュール・メール管理に使用）
認証: OAuth 2.0（Google OAuth）
"""

from __future__ import annotations

import logging
from typing import Any

from server.saas_mcp.base import (
    AuthMethod,
    ConnectionStatus,
    SaaSCredentials,
    SaaSMCPAdapter,
    SaaSToolInfo,
)
from server.saas_mcp.registry import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class GoogleWorkspaceAdapter(SaaSMCPAdapter):
    """Google Workspace MCP アダプタ."""

    saas_name = "google_workspace"
    display_name = "Google Workspace"
    genre = "productivity"
    supported_auth_methods = [AuthMethod.OAUTH2]
    default_scopes = [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    mcp_server_type = "community"
    description = "Gmail送受信・Googleカレンダー管理・Googleドライブ操作・スプレッドシート編集"

    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("Google Workspace: access_token が必要です")

        # TODO: Google OAuth2 API でトークン検証
        # GET https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=...
        self._status = ConnectionStatus.CONNECTED
        logger.info("Google Workspace 接続完了")

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("Google Workspace 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            # Gmail
            SaaSToolInfo(
                name="gmail_send",
                description="Gmail でメールを送信する",
                parameters={"to": "string", "subject": "string", "body": "string"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            SaaSToolInfo(
                name="gmail_search",
                description="Gmail でメールを検索する",
                parameters={"query": "string", "max_results": "int (optional, default: 10)"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            SaaSToolInfo(
                name="gmail_read",
                description="Gmail のメール内容を読み取る",
                parameters={"message_id": "string"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            # Google Calendar
            SaaSToolInfo(
                name="gcal_list_events",
                description="Google Calendar の予定一覧を取得する",
                parameters={"time_min": "string (ISO8601)", "time_max": "string (ISO8601)"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            SaaSToolInfo(
                name="gcal_create_event",
                description="Google Calendar に予定を作成する",
                parameters={"summary": "string", "start": "string", "end": "string", "attendees": "array (optional)"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            # Google Drive
            SaaSToolInfo(
                name="gdrive_list_files",
                description="Google Drive のファイル一覧を取得する",
                parameters={"query": "string (optional)", "folder_id": "string (optional)"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            SaaSToolInfo(
                name="gdrive_read_file",
                description="Google Drive のファイル内容を読み取る",
                parameters={"file_id": "string"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            # Google Sheets
            SaaSToolInfo(
                name="gsheets_read",
                description="Google Sheets のデータを読み取る",
                parameters={"spreadsheet_id": "string", "range": "string"},
                genre="productivity",
                saas_name="google_workspace",
            ),
            SaaSToolInfo(
                name="gsheets_write",
                description="Google Sheets にデータを書き込む",
                parameters={"spreadsheet_id": "string", "range": "string", "values": "array"},
                genre="productivity",
                saas_name="google_workspace",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(f"Google Workspace ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        return {
            "saas_name": "google_workspace",
            "schema_type": "services",
            "services": {
                "gmail": ["messages", "threads", "labels", "drafts"],
                "calendar": ["events", "calendars"],
                "drive": ["files", "folders", "permissions"],
                "sheets": ["spreadsheets", "sheets", "values"],
            },
        }

    def get_oauth_authorize_url(self, redirect_uri: str, state: str) -> str | None:
        scopes = "%20".join(self.default_scopes)
        return (
            f"{self.AUTHORIZE_URL}"
            f"?response_type=code"
            f"&client_id={{CLIENT_ID}}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={scopes}"
            f"&state={state}"
            f"&access_type=offline"
            f"&prompt=consent"
        )

    async def refresh_token(self) -> SaaSCredentials | None:
        if not self._credentials or not self._credentials.refresh_token:
            return None
        # TODO: POST https://oauth2.googleapis.com/token でトークンリフレッシュ
        return None

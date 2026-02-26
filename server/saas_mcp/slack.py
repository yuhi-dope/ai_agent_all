"""Slack MCP アダプタ.

Slack のメッセージ送信・チャンネル管理・リアクション等をMCP経由で操作する。
公式 Slack MCP サーバーを活用。

対応ジャンル: communication（全ジャンル横断で通知・報告に使用）
認証: OAuth 2.0（Bot Token）
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
class SlackAdapter(SaaSMCPAdapter):
    """Slack MCP アダプタ."""

    saas_name = "slack"
    display_name = "Slack"
    genre = "communication"
    supported_auth_methods = [AuthMethod.OAUTH2]
    default_scopes = [
        "chat:write", "channels:read", "channels:history",
        "users:read", "reactions:write", "files:write",
    ]
    mcp_server_type = "official"
    description = "メッセージ送信・チャンネル管理・ファイル共有・リアクション・業務報告通知"

    AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
    TOKEN_URL = "https://slack.com/api/oauth.v2.access"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("Slack: bot token が必要です")

        # TODO: Slack API でトークン検証
        # POST /api/auth.test
        self._status = ConnectionStatus.CONNECTED
        logger.info("Slack 接続完了")

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("Slack 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            SaaSToolInfo(
                name="slack_send_message",
                description="Slack チャンネルにメッセージを送信する",
                parameters={"channel": "string", "text": "string", "blocks": "array (optional)"},
                genre="communication",
                saas_name="slack",
            ),
            SaaSToolInfo(
                name="slack_list_channels",
                description="チャンネル一覧を取得する",
                parameters={"types": "string (optional, default: public_channel)"},
                genre="communication",
                saas_name="slack",
            ),
            SaaSToolInfo(
                name="slack_get_channel_history",
                description="チャンネルのメッセージ履歴を取得する",
                parameters={"channel": "string", "limit": "int (optional, default: 20)"},
                genre="communication",
                saas_name="slack",
            ),
            SaaSToolInfo(
                name="slack_add_reaction",
                description="メッセージにリアクションを追加する",
                parameters={"channel": "string", "timestamp": "string", "name": "string"},
                genre="communication",
                saas_name="slack",
            ),
            SaaSToolInfo(
                name="slack_upload_file",
                description="Slack にファイルをアップロードする",
                parameters={"channels": "string", "content": "string", "filename": "string"},
                genre="communication",
                saas_name="slack",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(f"Slack ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        return {
            "saas_name": "slack",
            "schema_type": "objects",
            "objects": [
                "channels", "users", "messages", "files",
                "reactions", "threads",
            ],
        }

    def get_oauth_authorize_url(self, redirect_uri: str, state: str) -> str | None:
        scopes = ",".join(self.default_scopes)
        return (
            f"{self.AUTHORIZE_URL}"
            f"?client_id={{CLIENT_ID}}"
            f"&scope={scopes}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

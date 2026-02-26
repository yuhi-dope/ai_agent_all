"""Slack MCP アダプタ.

Slack のメッセージ送信・チャンネル管理・リアクション等をMCP経由で操作する。
公式 Slack MCP サーバーを活用。

対応ジャンル: communication（全ジャンル横断で通知・報告に使用）
認証: OAuth 2.0（Bot Token）
"""

from __future__ import annotations

import logging
from typing import Any

from server.saas.mcp.base import (
    AuthMethod,
    ConnectionStatus,
    SaaSCredentials,
    SaaSMCPAdapter,
    SaaSToolInfo,
)
from server.saas.mcp.registry import register_adapter

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

    BASE_URL = "https://slack.com/api"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("Slack: bot token が必要です")

        self._access_token = credentials.access_token
        self._instance_url = self.BASE_URL

        # Slack API でトークン検証
        result = await self._api_request("POST", f"{self.BASE_URL}/auth.test")
        if not result.get("ok"):
            raise ConnectionError(f"Slack: auth.test 失敗 - {result.get('error')}")
        self._status = ConnectionStatus.CONNECTED
        logger.info("Slack 接続完了")

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("Slack 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        try:
            result = await self._api_request("POST", f"{self.BASE_URL}/auth.test")
            return result.get("ok", False)
        except Exception:
            logger.warning("Slack health_check 失敗", exc_info=True)
            return False

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
        base = self.BASE_URL

        if tool_name == "slack_send_message":
            payload: dict[str, Any] = {
                "channel": arguments["channel"],
                "text": arguments["text"],
            }
            if arguments.get("blocks"):
                payload["blocks"] = arguments["blocks"]
            return await self._api_request("POST", f"{base}/chat.postMessage", json=payload)

        if tool_name == "slack_list_channels":
            params: dict[str, Any] = {}
            if arguments.get("types"):
                params["types"] = arguments["types"]
            return await self._api_request("GET", f"{base}/conversations.list", params=params)

        if tool_name == "slack_get_channel_history":
            params = {"channel": arguments["channel"]}
            if arguments.get("limit"):
                params["limit"] = arguments["limit"]
            return await self._api_request("GET", f"{base}/conversations.history", params=params)

        if tool_name == "slack_add_reaction":
            return await self._api_request("POST", f"{base}/reactions.add", json={
                "channel": arguments["channel"],
                "timestamp": arguments["timestamp"],
                "name": arguments["name"],
            })

        if tool_name == "slack_upload_file":
            import httpx

            headers = {"Authorization": f"Bearer {self._access_token}"}
            data = {
                "channels": arguments.get("channels", ""),
                "filename": arguments.get("filename", "file.txt"),
            }
            files = {"content": ("file", arguments.get("content", "").encode())}
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base}/files.uploadV2",
                    headers=headers,
                    data=data,
                    files=files,
                )
                resp.raise_for_status()
                return resp.json()

        raise ValueError(f"Slack: 不明なツール '{tool_name}'")

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

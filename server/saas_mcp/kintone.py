"""kintone MCP アダプタ.

kintone のアプリ（レコード・フィールド・ビュー）をMCP経由で操作する。
公式 kintone MCP サーバーが利用可能。

対応ジャンル: admin（業務基盤として全ジャンルで活用可能）
認証: API トークン or OAuth 2.0
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
class KintoneAdapter(SaaSMCPAdapter):
    """kintone MCP アダプタ."""

    saas_name = "kintone"
    display_name = "kintone"
    genre = "admin"
    supported_auth_methods = [AuthMethod.API_KEY, AuthMethod.OAUTH2]
    default_scopes = [
        "k:app_record:read", "k:app_record:write",
        "k:app_settings:read", "k:app_settings:write",
        "k:file:read", "k:file:write",
    ]
    mcp_server_type = "official"
    description = "アプリのレコード操作・フィールド管理・ビュー取得・プロセス管理"

    # kintone は subdomain ベース: https://{subdomain}.cybozu.com
    AUTHORIZE_URL = "https://{subdomain}.cybozu.com/oauth2/authorization"
    TOKEN_URL = "https://{subdomain}.cybozu.com/oauth2/token"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token and not credentials.api_key:
            raise ConnectionError("kintone: access_token または api_key が必要です")

        if not credentials.instance_url:
            raise ConnectionError("kintone: instance_url（https://xxx.cybozu.com）が必要です")

        # TODO: kintone REST API でトークン検証
        self._status = ConnectionStatus.CONNECTED
        logger.info("kintone 接続完了: %s", credentials.instance_url)

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("kintone 切断")

    async def health_check(self) -> bool:
        if not self._credentials:
            return False
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            SaaSToolInfo(
                name="kintone_get_records",
                description="kintone アプリのレコード一覧を取得する",
                parameters={"app_id": "int", "query": "string (optional)", "fields": "array (optional)"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_add_record",
                description="kintone アプリにレコードを追加する",
                parameters={"app_id": "int", "record": "object"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_update_record",
                description="kintone アプリのレコードを更新する",
                parameters={"app_id": "int", "record_id": "int", "record": "object"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_get_app_fields",
                description="kintone アプリのフィールド定義を取得する",
                parameters={"app_id": "int"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_get_apps",
                description="kintone スペース内のアプリ一覧を取得する",
                parameters={"space_id": "int (optional)"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_update_status",
                description="kintone プロセス管理のステータスを更新する",
                parameters={"app_id": "int", "record_id": "int", "action": "string"},
                genre="admin",
                saas_name="kintone",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(f"kintone ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        return {
            "saas_name": "kintone",
            "schema_type": "apps",
            "objects": [
                "apps", "records", "fields", "views",
                "process_management", "spaces",
            ],
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
        )

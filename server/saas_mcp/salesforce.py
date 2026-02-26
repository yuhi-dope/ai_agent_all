"""Salesforce MCP アダプタ.

Salesforce の商談・取引先・リード等をMCP経由で操作する。
公式 Salesforce MCP サーバー（@anthropic/salesforce-mcp）を活用。

対応ジャンル: SFA, CRM
認証: OAuth 2.0（Web Server Flow）
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
class SalesforceAdapter(SaaSMCPAdapter):
    """Salesforce MCP アダプタ."""

    saas_name = "salesforce"
    display_name = "Salesforce"
    genre = "sfa"
    supported_auth_methods = [AuthMethod.OAUTH2]
    default_scopes = ["api", "refresh_token", "offline_access"]
    mcp_server_type = "official"
    description = "商談管理・取引先管理・リード管理・レポート取得"

    # Salesforce OAuth エンドポイント
    AUTHORIZE_URL = "https://login.salesforce.com/services/oauth2/authorize"
    TOKEN_URL = "https://login.salesforce.com/services/oauth2/token"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("Salesforce: access_token が必要です")

        if not credentials.instance_url:
            raise ConnectionError("Salesforce: instance_url が必要です")

        # TODO: MultiServerMCPClient 経由で Salesforce MCP サーバーに接続
        # mcp_client = MultiServerMCPClient({
        #     "salesforce": {
        #         "url": f"{credentials.instance_url}/services/mcp",
        #         "transport": "streamable_http",
        #         "headers": {"Authorization": f"Bearer {credentials.access_token}"},
        #     }
        # })
        self._status = ConnectionStatus.CONNECTED
        logger.info("Salesforce 接続完了: %s", credentials.instance_url)

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("Salesforce 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        # TODO: Salesforce API でトークン有効性を確認
        # GET /services/oauth2/userinfo
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            SaaSToolInfo(
                name="sf_query",
                description="SOQL クエリを実行して Salesforce データを取得",
                parameters={"query": "string (SOQL)"},
                genre="sfa",
                saas_name="salesforce",
            ),
            SaaSToolInfo(
                name="sf_create_record",
                description="Salesforce オブジェクトのレコードを作成",
                parameters={"object_type": "string", "fields": "object"},
                genre="sfa",
                saas_name="salesforce",
            ),
            SaaSToolInfo(
                name="sf_update_record",
                description="Salesforce レコードを更新",
                parameters={"object_type": "string", "record_id": "string", "fields": "object"},
                genre="sfa",
                saas_name="salesforce",
            ),
            SaaSToolInfo(
                name="sf_get_opportunity_pipeline",
                description="商談パイプライン（ステージ別集計）を取得",
                parameters={"filters": "object (optional)"},
                genre="sfa",
                saas_name="salesforce",
            ),
            SaaSToolInfo(
                name="sf_describe_object",
                description="Salesforce オブジェクトのメタデータ（フィールド・リレーション）を取得",
                parameters={"object_type": "string"},
                genre="sfa",
                saas_name="salesforce",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        # TODO: MCP サーバー経由で実行
        raise NotImplementedError(f"Salesforce ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        # TODO: Salesforce Describe API でスキーマ取得
        # GET /services/data/vXX.0/sobjects/ → 全オブジェクト一覧
        # GET /services/data/vXX.0/sobjects/{object}/describe/ → フィールド詳細
        return {
            "saas_name": "salesforce",
            "schema_type": "objects",
            "objects": [
                "Account", "Contact", "Opportunity", "Lead",
                "Case", "Task", "Event", "Campaign",
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

    async def refresh_token(self) -> SaaSCredentials | None:
        if not self._credentials or not self._credentials.refresh_token:
            return None
        # TODO: POST /services/oauth2/token でトークンリフレッシュ
        return None

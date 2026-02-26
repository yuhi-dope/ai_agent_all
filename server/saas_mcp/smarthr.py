"""SmartHR MCP アダプタ.

SmartHR の従業員情報・入退社手続き・年末調整等をMCP経由で操作する。
SmartHR は REST API を公開しており、カスタム MCP サーバーとして実装。

対応ジャンル: admin（人事労務）
認証: OAuth 2.0
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
class SmartHRAdapter(SaaSMCPAdapter):
    """SmartHR MCP アダプタ."""

    saas_name = "smarthr"
    display_name = "SmartHR"
    genre = "admin"
    supported_auth_methods = [AuthMethod.OAUTH2]
    default_scopes = ["read", "write"]
    mcp_server_type = "custom"
    description = "従業員情報管理・入退社手続き・年末調整・給与明細配付・組織図管理"

    # SmartHR は subdomain ベース: https://{subdomain}.smarthr.jp
    AUTHORIZE_URL = "https://{subdomain}.smarthr.jp/oauth/authorization"
    TOKEN_URL = "https://{subdomain}.smarthr.jp/oauth/token"
    API_BASE = "https://{subdomain}.smarthr.jp/api/v1"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("SmartHR: access_token が必要です")

        if not credentials.instance_url:
            raise ConnectionError("SmartHR: instance_url（https://xxx.smarthr.jp）が必要です")

        # TODO: SmartHR API でトークン検証
        self._status = ConnectionStatus.CONNECTED
        logger.info("SmartHR 接続完了: %s", credentials.instance_url)

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("SmartHR 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            SaaSToolInfo(
                name="smarthr_list_crews",
                description="従業員一覧を取得する",
                parameters={"page": "int (optional)", "per_page": "int (optional)", "status": "string (optional)"},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_get_crew",
                description="従業員の詳細情報を取得する",
                parameters={"crew_id": "string"},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_create_crew",
                description="従業員を新規登録する（入社手続き）",
                parameters={"last_name": "string", "first_name": "string", "email": "string", "department_id": "string (optional)"},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_update_crew",
                description="従業員情報を更新する",
                parameters={"crew_id": "string", "fields": "object"},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_list_departments",
                description="部署一覧を取得する",
                parameters={},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_list_employment_types",
                description="雇用形態一覧を取得する",
                parameters={},
                genre="admin",
                saas_name="smarthr",
            ),
            SaaSToolInfo(
                name="smarthr_get_payroll_statement",
                description="給与明細を取得する",
                parameters={"crew_id": "string", "year": "int", "month": "int"},
                genre="admin",
                saas_name="smarthr",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(f"SmartHR ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        return {
            "saas_name": "smarthr",
            "schema_type": "objects",
            "objects": [
                "crews", "departments", "employment_types",
                "job_titles", "pay_slips", "dependents",
                "bank_accounts", "custom_fields",
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
        # TODO: POST /oauth/token でトークンリフレッシュ
        return None

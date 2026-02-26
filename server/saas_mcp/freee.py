"""freee MCP アダプタ.

freee の会計（仕訳・取引先・勘定科目）、人事労務、給与等をMCP経由で操作する。
freee は 5 API（会計・人事労務・給与・マイナンバー・工数）を公開。

対応ジャンル: accounting
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
class FreeeAdapter(SaaSMCPAdapter):
    """freee MCP アダプタ."""

    saas_name = "freee"
    display_name = "freee会計"
    genre = "accounting"
    supported_auth_methods = [AuthMethod.OAUTH2]
    default_scopes = ["read", "write"]
    mcp_server_type = "community"
    description = "仕訳作成・取引先管理・勘定科目管理・月次決算・請求書発行"

    BASE_URL = "https://api.freee.co.jp"
    AUTHORIZE_URL = "https://accounts.secure.freee.co.jp/public_api/authorize"
    TOKEN_URL = "https://accounts.secure.freee.co.jp/public_api/token"

    async def connect(self, credentials: SaaSCredentials) -> None:
        self._credentials = credentials
        self._status = ConnectionStatus.CONNECTING

        if not credentials.access_token:
            raise ConnectionError("freee: access_token が必要です")

        # TODO: freee API でトークン検証
        # GET /api/1/users/me
        self._status = ConnectionStatus.CONNECTED
        logger.info("freee 接続完了")

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("freee 切断")

    async def health_check(self) -> bool:
        if not self._credentials or not self._credentials.access_token:
            return False
        return self._status == ConnectionStatus.CONNECTED

    async def get_available_tools(self) -> list[SaaSToolInfo]:
        return [
            SaaSToolInfo(
                name="freee_create_journal",
                description="仕訳を作成する",
                parameters={"company_id": "int", "details": "array"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_list_journals",
                description="仕訳一覧を取得する",
                parameters={"company_id": "int", "start_date": "string", "end_date": "string"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_list_partners",
                description="取引先一覧を取得する",
                parameters={"company_id": "int"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_create_invoice",
                description="請求書を作成する",
                parameters={"company_id": "int", "partner_id": "int", "items": "array"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_get_trial_balance",
                description="試算表（残高試算表）を取得する",
                parameters={"company_id": "int", "fiscal_year": "int"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_list_account_items",
                description="勘定科目一覧を取得する",
                parameters={"company_id": "int"},
                genre="accounting",
                saas_name="freee",
            ),
            SaaSToolInfo(
                name="freee_reconcile",
                description="入出金消込を実行する",
                parameters={"company_id": "int", "bank_transaction_id": "int", "deal_id": "int"},
                genre="accounting",
                saas_name="freee",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError(f"freee ツール '{tool_name}' は未実装です")

    async def get_schema(self) -> dict[str, Any]:
        return {
            "saas_name": "freee",
            "schema_type": "objects",
            "objects": [
                "deals", "journal_entries", "partners", "account_items",
                "taxes", "invoices", "expense_applications", "banks",
                "sections", "tags", "walletables",
            ],
        }

    def get_oauth_authorize_url(self, redirect_uri: str, state: str) -> str | None:
        return (
            f"{self.AUTHORIZE_URL}"
            f"?response_type=code"
            f"&client_id={{CLIENT_ID}}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

    async def refresh_token(self) -> SaaSCredentials | None:
        if not self._credentials or not self._credentials.refresh_token:
            return None
        # TODO: POST /public_api/token でトークンリフレッシュ
        return None

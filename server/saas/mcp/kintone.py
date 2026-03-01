"""kintone MCP アダプタ.

kintone のアプリ（レコード・フィールド・ビュー）をMCP経由で操作する。
公式 kintone MCP サーバーが利用可能。

対応ジャンル: admin（業務基盤として全ジャンルで活用可能）
認証: API トークン or OAuth 2.0
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

        self._access_token = credentials.access_token
        self._instance_url = credentials.instance_url

        # kintone REST API でトークン検証
        # 401 はトークン期限切れ → health_check で false を返しリフレッシュに委ねる
        # 403 はスコープ不足の可能性があるが接続自体は許可
        self._token_valid = True
        import httpx
        try:
            await self._kintone_request("GET", "/k/v1/apps.json", params={"limit": "1"})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.warning("kintone 接続: 401（トークン期限切れ）。リフレッシュが必要です。")
                self._token_valid = False
            elif e.response.status_code == 403:
                logger.warning(
                    "kintone 接続: /k/v1/apps.json に 403（スコープ不足の可能性）。"
                    "トークン自体は有効と判断し接続を継続します。"
                )
            else:
                raise
        self._status = ConnectionStatus.CONNECTED
        logger.info("kintone 接続完了: %s", credentials.instance_url)

    async def disconnect(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials = None
        logger.info("kintone 切断")

    async def _kintone_request(self, method: str, path: str, **kwargs) -> dict:
        """kintone API リクエスト（API トークン / OAuth 両対応）."""
        import httpx

        url = f"{self._instance_url}{path}"
        headers = kwargs.pop("headers", {})
        if method.upper() != "GET":
            headers.setdefault("Content-Type", "application/json")
        if self._credentials and self._credentials.api_key:
            headers.setdefault("X-Cybozu-API-Token", self._credentials.api_key)
        elif self._access_token:
            headers.setdefault("Authorization", f"Bearer {self._access_token}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code >= 400:
                detail = resp.text[:500] if resp.text else "No detail"
                if resp.status_code == 403:
                    msg = (
                        f"kintone API 403 Forbidden: {path} - アクセス権限がありません。"
                        f"対処法: (1) ダッシュボードでkintoneを切断→再接続し、認可画面で全てのスコープを許可してください。"
                        f" (2) kintoneにログインしているアカウントが対象アプリへのアクセス権を持っているか確認してください。"
                        f" Detail: {detail}"
                    )
                else:
                    msg = f"kintone API {resp.status_code}: {path} - {detail}"
                raise httpx.HTTPStatusError(
                    msg, request=resp.request, response=resp,
                )
            if resp.status_code == 204:
                return {"success": True}
            return resp.json()

    async def health_check(self) -> bool:
        if not self._credentials:
            return False
        if not getattr(self, '_token_valid', True):
            return False
        try:
            await self._kintone_request("GET", "/k/v1/apps.json", params={"limit": "1"})
            return True
        except Exception as e:
            # 403 はトークン有効だがスコープ不足 → 認証自体は成功
            import httpx
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
                logger.warning("kintone health_check: 403（スコープ不足の可能性）だがトークンは有効と判断")
                return True
            logger.warning("kintone health_check 失敗", exc_info=True)
            return False

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
            SaaSToolInfo(
                name="kintone_add_fields",
                description="kintone アプリにフィールドを追加する（プレビュー環境）。追加後は kintone_deploy_app でデプロイが必要",
                parameters={"app_id": "int", "fields": "object (フィールドコード→定義のマップ)"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_deploy_app",
                description="kintone アプリの設定変更（フィールド追加等）を本番環境にデプロイする",
                parameters={"app_id": "int"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_add_records",
                description="kintone アプリに複数レコードを一括追加する（最大100件）",
                parameters={"app_id": "int", "records": "array of objects"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_get_layout",
                description="kintone アプリのフォームレイアウト（フィールド配置）を取得する",
                parameters={"app_id": "int"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_update_layout",
                description="kintone アプリのフォームレイアウトを更新する（プレビュー環境）。更新後は kintone_deploy_app でデプロイが必要。全フィールドを指定すること（未指定フィールドはレイアウトから除外される）",
                parameters={"app_id": "int", "layout": "array of row objects"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_get_views",
                description="kintone アプリのビュー（一覧）設定を取得する",
                parameters={"app_id": "int"},
                genre="admin",
                saas_name="kintone",
            ),
            SaaSToolInfo(
                name="kintone_update_views",
                description="kintone アプリのビュー設定を更新する（プレビュー環境）。更新後は kintone_deploy_app でデプロイが必要。全ビューを指定すること（未指定ビューは削除される）",
                parameters={"app_id": "int", "views": "object (ビュー名→設定のマップ)"},
                genre="admin",
                saas_name="kintone",
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name == "kintone_get_records":
            params: dict[str, Any] = {"app": arguments["app_id"]}
            if arguments.get("query"):
                params["query"] = arguments["query"]
            if arguments.get("fields"):
                params["fields"] = arguments["fields"]
            return await self._kintone_request(
                "GET", "/k/v1/records.json", params=params,
            )

        if tool_name == "kintone_add_record":
            return await self._kintone_request(
                "POST", "/k/v1/record.json",
                json={"app": arguments["app_id"], "record": arguments["record"]},
            )

        if tool_name == "kintone_update_record":
            return await self._kintone_request(
                "PUT", "/k/v1/record.json",
                json={
                    "app": arguments["app_id"],
                    "id": arguments["record_id"],
                    "record": arguments["record"],
                },
            )

        if tool_name == "kintone_get_app_fields":
            return await self._kintone_request(
                "GET", "/k/v1/app/form/fields.json",
                params={"app": arguments["app_id"]},
            )

        if tool_name == "kintone_get_apps":
            params = {}
            if arguments.get("space_id"):
                params["spaceIds"] = [arguments["space_id"]]
            return await self._kintone_request(
                "GET", "/k/v1/apps.json", params=params,
            )

        if tool_name == "kintone_update_status":
            return await self._kintone_request(
                "PUT", "/k/v1/record/status.json",
                json={
                    "app": arguments["app_id"],
                    "id": arguments["record_id"],
                    "action": arguments["action"],
                },
            )

        if tool_name == "kintone_add_fields":
            return await self._kintone_request(
                "POST", "/k/v1/preview/app/form/fields.json",
                json={
                    "app": arguments["app_id"],
                    "properties": arguments["fields"],
                },
            )

        if tool_name == "kintone_deploy_app":
            return await self._kintone_request(
                "POST", "/k/v1/preview/app/deploy.json",
                json={"apps": [{"app": arguments["app_id"]}]},
            )

        if tool_name == "kintone_add_records":
            return await self._kintone_request(
                "POST", "/k/v1/records.json",
                json={
                    "app": arguments["app_id"],
                    "records": arguments["records"],
                },
            )

        if tool_name == "kintone_get_layout":
            return await self._kintone_request(
                "GET", "/k/v1/app/form/layout.json",
                params={"app": arguments["app_id"]},
            )

        if tool_name == "kintone_update_layout":
            return await self._kintone_request(
                "PUT", "/k/v1/preview/app/form/layout.json",
                json={
                    "app": arguments["app_id"],
                    "layout": arguments["layout"],
                },
            )

        if tool_name == "kintone_get_views":
            return await self._kintone_request(
                "GET", "/k/v1/app/views.json",
                params={"app": arguments["app_id"]},
            )

        if tool_name == "kintone_update_views":
            return await self._kintone_request(
                "PUT", "/k/v1/preview/app/views.json",
                json={
                    "app": arguments["app_id"],
                    "views": arguments["views"],
                },
            )

        raise ValueError(f"kintone: 不明なツール '{tool_name}'")

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

"""SaaS操作実行エンジン.

ToolRegistry 経由でツールを実行し、監査ログを記録する。
全てのSaaS操作はこのモジュールを経由して実行される。

使用例:
    executor = SaaSExecutor(company_id="xxx", connection_id="yyy")
    await executor.initialize()
    result = await executor.execute("freee_create_journal", {"company_id": 1, "details": [...]})
    audit_log = executor.get_audit_log()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from server.saas.tools.http import SaaSCreds
from server.saas.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 読み取り系ツール → 構造ナレッジとして自動蓄積する対象マッピング
# ツール名 → (structure_type, entity_id を引数から取得する関数)
KNOWLEDGE_CAPTURE_TOOLS: dict[str, tuple[str, Any]] = {
    "kintone_get_app_fields": ("fields", lambda args: str(args["app_id"])),
    "kintone_get_layout": ("layout", lambda args: str(args["app_id"])),
    "kintone_get_views": ("views", lambda args: str(args["app_id"])),
    "kintone_get_apps": ("objects", lambda _: "_all"),
    "kintone_get_records": ("records_sample", lambda args: str(args["app_id"])),
    "sf_describe_object": ("objects", lambda args: args.get("object_type", "_all")),
    "freee_list_account_items": ("objects", lambda _: "account_items"),
    "smarthr_list_departments": ("objects", lambda _: "departments"),
    "smarthr_list_employment_types": ("objects", lambda _: "employment_types"),
}


class SaaSExecutor:
    """SaaS操作を実行し、監査ログを記録するエンジン."""

    def __init__(self, company_id: str, connection_id: str) -> None:
        self.company_id = company_id
        self.connection_id = connection_id
        self._creds: SaaSCreds | None = None
        self._connection_data: dict | None = None
        self._audit_log: list[dict] = []
        self._initialized = False

    async def initialize(self) -> None:
        """接続情報を読み込み、認証情報を準備する."""
        from server.saas import connection as saas_connection

        self._connection_data = saas_connection.get_connection(
            self.connection_id, company_id=self.company_id
        )
        if not self._connection_data:
            raise ValueError(f"SaaS接続が見つかりません: {self.connection_id}")

        saas_name = self._connection_data["saas_name"]

        # ToolRegistry にツールが存在するか確認
        tools = ToolRegistry.list_tools(saas_name)
        if not tools:
            raise ValueError(f"未対応のSaaS: {saas_name}")

        # トークンが期限切れなら先にリフレッシュを試みる
        await self._ensure_fresh_token()

        # 認証情報をロード
        self._creds = await self._load_credentials()

        # ヘルスチェック（軽量な読み取りAPIで検証）
        await self._health_check(saas_name, saas_connection)

        saas_connection.update_last_used(self.connection_id)
        self._initialized = True

    async def _health_check(self, saas_name: str, saas_connection) -> None:
        """軽量APIでトークン有効性を確認する."""
        import httpx

        check_urls = {
            "salesforce": lambda c: f"{c.instance_url}/services/oauth2/userinfo",
            "freee": lambda _: "https://api.freee.co.jp/api/1/users/me",
            "slack": lambda _: "https://slack.com/api/auth.test",
            "google_workspace": lambda c: f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={c.access_token}",
            "smarthr": lambda c: f"{c.instance_url}/api/v1/users/me",
            "kintone": lambda c: f"{c.instance_url}/k/v1/apps.json?limit=1",
        }

        url_fn = check_urls.get(saas_name)
        if not url_fn or not self._creds:
            return  # ヘルスチェック対象外

        url = url_fn(self._creds)
        headers = {}
        if self._creds.api_key:
            headers["X-Cybozu-API-Token"] = self._creds.api_key
        elif self._creds.access_token:
            headers["Authorization"] = f"Bearer {self._creds.access_token}"

        method = "POST" if saas_name == "slack" else "GET"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request(method, url, headers=headers)
                if resp.status_code == 401:
                    # トークン期限切れ → リフレッシュして再試行
                    logger.warning("%s ヘルスチェック 401、リフレッシュ試行", saas_name)
                    refreshed = await self._try_refresh_token()
                    if refreshed:
                        self._creds = await self._load_credentials()
                        return  # リフレッシュ成功なら OK
                    saas_connection.update_status(self.connection_id, "token_expired")
                    raise ConnectionError(f"{saas_name} のトークンが無効です。再認証してください。")
                if resp.status_code == 403 and saas_name == "kintone":
                    logger.warning("kintone ヘルスチェック: 403（スコープ不足の可能性）だがトークンは有効と判断")
                    return
        except httpx.HTTPStatusError:
            raise
        except ConnectionError:
            raise
        except Exception:
            logger.warning("%s ヘルスチェック失敗", saas_name, exc_info=True)

    async def _ensure_fresh_token(self) -> None:
        """トークンが期限切れ間近なら事前にリフレッシュする."""
        from server import oauth_store
        from server.saas.token_refresh import _needs_refresh

        conn = self._connection_data
        saas_name = conn["saas_name"]
        provider = f"saas_{saas_name}"
        token_data = oauth_store.get_token(provider=provider, tenant_id=self.company_id)

        if token_data and _needs_refresh(token_data):
            logger.info("%s トークン期限切れ間近、リフレッシュ実行", saas_name)
            await self._try_refresh_token()

    async def _try_refresh_token(self) -> bool:
        """トークンリフレッシュを試みる。成功なら True."""
        from server.saas.token_refresh import _refresh_token

        conn = self._connection_data
        saas_name = conn["saas_name"]

        from server import oauth_store
        token_data = oauth_store.get_token(
            provider=f"saas_{saas_name}", tenant_id=self.company_id
        )
        if not token_data or not token_data.get("refresh_token"):
            logger.warning("%s リフレッシュトークンなし", saas_name)
            return False

        instance_url = conn.get("instance_url")
        if not instance_url:
            from server.channel_config import get_config_value
            instance_url = get_config_value(self.company_id, saas_name, "instance_url")

        return await _refresh_token(
            saas_name=saas_name,
            company_id=self.company_id,
            connection_id=self.connection_id,
            refresh_token=token_data["refresh_token"],
            instance_url=instance_url,
        )

    async def _load_credentials(self) -> SaaSCreds:
        """接続情報からOAuthトークンを読み込む."""
        from server import oauth_store

        conn = self._connection_data
        saas_name = conn["saas_name"]

        provider = f"saas_{saas_name}"
        token_data = oauth_store.get_token(provider=provider, tenant_id=self.company_id)

        access_token = None
        if token_data:
            access_token = token_data.get("access_token")

        instance_url = conn.get("instance_url")
        if not instance_url:
            from server.channel_config import get_config_value
            instance_url = get_config_value(self.company_id, saas_name, "instance_url")
            if instance_url:
                logger.info("instance_url を channel_configs から取得: %s", saas_name)

        api_key = conn.get("api_key")

        return SaaSCreds(
            access_token=access_token,
            api_key=api_key,
            instance_url=instance_url,
        )

    async def execute(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """SaaSツールを実行し、監査ログに記録する."""
        if not self._initialized:
            raise RuntimeError("未初期化です。initialize() を先に呼んでください")

        started_at = datetime.now(timezone.utc)

        try:
            result = await ToolRegistry.execute(
                tool_name, arguments, creds=self._creds,
            )
            success = result.get("success", True)
            error = None
            if "success" not in result:
                result["success"] = success
        except NotImplementedError:
            result = {"success": False, "error": "not_implemented", "tool": tool_name}
            success = False
            error = "not_implemented"
        except Exception as e:
            result = {"success": False, "error": str(e)}
            success = False
            error = str(e)

        ended_at = datetime.now(timezone.utc)
        duration_ms = int((ended_at - started_at).total_seconds() * 1000)

        audit_record = {
            "timestamp": started_at.isoformat(),
            "tool": tool_name,
            "arguments": arguments,
            "result_summary": {
                "success": success,
                "error": error,
                "duration_ms": duration_ms,
            },
            "saas_name": self._connection_data["saas_name"],
            "genre": self._connection_data["genre"],
            "company_id": self.company_id,
            "connection_id": self.connection_id,
        }
        self._audit_log.append(audit_record)

        # 読み取り系ツールの結果を構造ナレッジとして自動蓄積
        if success and tool_name in KNOWLEDGE_CAPTURE_TOOLS:
            self._capture_knowledge(tool_name, arguments, result)

        return result

    def _capture_knowledge(
        self, tool_name: str, arguments: dict, result: dict,
    ) -> None:
        """読み取り系ツールの結果を saas_structure_knowledge に保存する."""
        try:
            from server.persist import upsert_structure_knowledge

            structure_type, entity_id_fn = KNOWLEDGE_CAPTURE_TOOLS[tool_name]
            entity_id = entity_id_fn(arguments)
            saas_name = self._connection_data["saas_name"]

            upsert_structure_knowledge(
                company_id=self.company_id,
                saas_name=saas_name,
                entity_id=entity_id,
                structure_type=structure_type,
                structure_data=result,
            )
            logger.debug(
                "構造ナレッジ蓄積: %s/%s/%s", saas_name, entity_id, structure_type,
            )
        except Exception:
            logger.warning("構造ナレッジ蓄積失敗", exc_info=True)

    async def get_schema(self) -> dict[str, Any]:
        """SaaSのデータ構造を取得する（Phase 2 構造学習用）."""
        saas_name = self._connection_data["saas_name"]
        meta = ToolRegistry.get_saas_metadata(saas_name)
        if meta and meta.schema_fn:
            return await meta.schema_fn()
        # フォールバック: ツール一覧からオブジェクト名を推定
        tools = ToolRegistry.list_tools(saas_name)
        return {
            "saas_name": saas_name,
            "schema_type": "tools",
            "objects": [t.name for t in tools],
        }

    def get_available_tools(self) -> list[dict]:
        """利用可能なツール一覧を返す."""
        saas_name = self._connection_data["saas_name"] if self._connection_data else None
        tools = ToolRegistry.list_tools(saas_name)
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    def get_audit_log(self) -> list[dict]:
        """蓄積された監査ログを返す."""
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        """監査ログをクリアする."""
        self._audit_log.clear()

    async def close(self) -> None:
        """リソースを解放する（ToolRegistry はステートレスなので何もしない）."""
        self._creds = None
        self._initialized = False


async def execute_saas_operation(
    company_id: str,
    connection_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """SaaS操作を実行し、監査ログをDBに永続化する（ワンショット実行）."""
    from server import persist

    executor = SaaSExecutor(company_id=company_id, connection_id=connection_id)
    try:
        await executor.initialize()
        result = await executor.execute(tool_name, arguments)
        audit_log = executor.get_audit_log()

        if audit_log:
            persist.persist_audit_logs(
                run_id=run_id or f"saas_{connection_id}",
                audit_records=audit_log,
                source="saas",
            )

        return {"result": result, "audit_log": audit_log}
    finally:
        await executor.close()

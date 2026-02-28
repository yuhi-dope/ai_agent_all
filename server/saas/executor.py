"""SaaS操作実行エンジン.

SaaS MCPアダプタ経由でツールを実行し、監査ログを記録する。
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

from server.saas.mcp import SaaSCredentials, get_adapter
from server.saas.mcp.base import AuthMethod

logger = logging.getLogger(__name__)


class SaaSExecutor:
    """SaaS操作を実行し、監査ログを記録するエンジン."""

    def __init__(self, company_id: str, connection_id: str) -> None:
        self.company_id = company_id
        self.connection_id = connection_id
        self._adapter = None
        self._connection_data: dict | None = None
        self._audit_log: list[dict] = []

    async def initialize(self) -> None:
        """接続情報を読み込み、アダプタを初期化する."""
        from server.saas import connection as saas_connection

        self._connection_data = saas_connection.get_connection(
            self.connection_id, company_id=self.company_id
        )
        if not self._connection_data:
            raise ValueError(f"SaaS接続が見つかりません: {self.connection_id}")

        saas_name = self._connection_data["saas_name"]
        self._adapter = get_adapter(saas_name)
        if not self._adapter:
            raise ValueError(f"未対応のSaaS: {saas_name}")

        # トークンが期限切れなら先にリフレッシュを試みる
        await self._ensure_fresh_token()

        # OAuth トークンを取得してアダプタに接続
        credentials = await self._load_credentials()
        await self._adapter.connect(credentials)

        # 接続確認
        is_healthy = await self._adapter.health_check()
        if not is_healthy:
            # ヘルスチェック失敗 → リフレッシュして再試行
            logger.warning("%s ヘルスチェック失敗、トークンリフレッシュを試行", saas_name)
            refreshed = await self._try_refresh_token()
            if refreshed:
                credentials = await self._load_credentials()
                await self._adapter.connect(credentials)
                is_healthy = await self._adapter.health_check()

            if not is_healthy:
                saas_connection.update_status(self.connection_id, "token_expired")
                raise ConnectionError(f"{saas_name} のトークンが無効です。再認証してください。")

        saas_connection.update_last_used(self.connection_id)

    async def _ensure_fresh_token(self) -> None:
        """トークンが期限切れ間近なら事前にリフレッシュする。"""
        from server import oauth_store
        from server.token_refresh import _needs_refresh

        conn = self._connection_data
        saas_name = conn["saas_name"]
        provider = f"saas_{saas_name}"
        token_data = oauth_store.get_token(provider=provider, tenant_id=self.company_id)

        if token_data and _needs_refresh(token_data):
            logger.info("%s トークン期限切れ間近、リフレッシュ実行", saas_name)
            await self._try_refresh_token()

    async def _try_refresh_token(self) -> bool:
        """トークンリフレッシュを試みる。成功なら True。"""
        from server.token_refresh import _refresh_token

        conn = self._connection_data
        saas_name = conn["saas_name"]
        provider = f"saas_{saas_name}"

        from server import oauth_store
        token_data = oauth_store.get_token(provider=provider, tenant_id=self.company_id)
        if not token_data or not token_data.get("refresh_token"):
            logger.warning("%s リフレッシュトークンなし", saas_name)
            return False

        return await _refresh_token(
            saas_name=saas_name,
            company_id=self.company_id,
            connection_id=self.connection_id,
            refresh_token=token_data["refresh_token"],
            instance_url=conn.get("instance_url"),
        )

    async def _load_credentials(self) -> SaaSCredentials:
        """接続情報からOAuthトークンを読み込む."""
        from server import oauth_store

        conn = self._connection_data
        saas_name = conn["saas_name"]

        # oauth_store から tenant_id=company_id でトークン取得
        # provider名は "saas_{saas_name}" とする（既存チャネルと区別）
        provider = f"saas_{saas_name}"
        token_data = oauth_store.get_token(provider=provider, tenant_id=self.company_id)

        access_token = None
        refresh_token = None
        if token_data:
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

        auth_method = AuthMethod(conn.get("auth_method", "oauth2"))

        return SaaSCredentials(
            auth_method=auth_method,
            access_token=access_token,
            refresh_token=refresh_token,
            instance_url=conn.get("instance_url"),
            scopes=conn.get("scopes") or [],
        )

    async def execute(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """SaaSツールを実行し、監査ログに記録する.

        Args:
            tool_name: ツール名（例: "freee_create_journal"）
            arguments: ツール引数

        Returns:
            実行結果
        """
        if not self._adapter or not self._adapter.is_connected:
            raise RuntimeError("アダプタが未初期化です。initialize() を先に呼んでください")

        started_at = datetime.now(timezone.utc)

        try:
            result = await self._adapter.execute_tool(tool_name, arguments)
            success = result.get("success", True)
            error = None
        except NotImplementedError:
            # スケルトン状態: 実際のAPI呼び出し未実装
            result = {"success": False, "error": "not_implemented", "tool": tool_name}
            success = False
            error = "not_implemented"
        except Exception as e:
            result = {"success": False, "error": str(e)}
            success = False
            error = str(e)

        ended_at = datetime.now(timezone.utc)
        duration_ms = int((ended_at - started_at).total_seconds() * 1000)

        # 監査ログ記録
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

        return result

    async def get_schema(self) -> dict[str, Any]:
        """SaaSのデータ構造を取得する（Phase 2 構造学習用）."""
        if not self._adapter or not self._adapter.is_connected:
            raise RuntimeError("アダプタが未初期化です")
        return await self._adapter.get_schema()

    async def get_available_tools(self) -> list[dict]:
        """利用可能なツール一覧を返す."""
        if not self._adapter:
            raise RuntimeError("アダプタが未初期化です")
        tools = await self._adapter.get_available_tools()
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
        """アダプタを切断する."""
        if self._adapter:
            await self._adapter.disconnect()
            self._adapter = None


async def execute_saas_operation(
    company_id: str,
    connection_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """SaaS操作を実行し、監査ログをDBに永続化する（ワンショット実行）.

    Args:
        company_id: 企業ID
        connection_id: SaaS接続ID
        tool_name: ツール名
        arguments: ツール引数
        run_id: 紐付けるrun ID（オプション）

    Returns:
        {"result": ..., "audit_log": [...]}
    """
    from server import persist

    executor = SaaSExecutor(company_id=company_id, connection_id=connection_id)
    try:
        await executor.initialize()
        result = await executor.execute(tool_name, arguments)
        audit_log = executor.get_audit_log()

        # 監査ログをDBに永続化
        if audit_log:
            persist.persist_audit_logs(
                run_id=run_id or f"saas_{connection_id}",
                audit_records=audit_log,
                source="saas",
            )

        return {"result": result, "audit_log": audit_log}
    finally:
        await executor.close()

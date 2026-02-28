"""SaaS接続管理の永続化層.

company_saas_connections テーブルのCRUD操作を提供する。
OAuth トークン本体は oauth_store.py で管理し、
このモジュールは接続メタデータ（SaaS名・ジャンル・MCP設定等）を管理する。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _get_client():
    from server._supabase import get_client
    return get_client()


def create_connection(
    company_id: str,
    saas_name: str,
    genre: str,
    auth_method: str,
    mcp_server_type: str,
    department: Optional[str] = None,
    token_secret_name: Optional[str] = None,
    mcp_server_config: Optional[dict] = None,
    instance_url: Optional[str] = None,
    scopes: Optional[list[str]] = None,
) -> dict | None:
    """SaaS接続を作成する."""
    client = _get_client()
    if not client:
        logger.warning("Supabase未設定: SaaS接続を保存できません")
        return None

    row = {
        "company_id": company_id,
        "saas_name": saas_name,
        "genre": genre,
        "auth_method": auth_method,
        "mcp_server_type": mcp_server_type,
        "status": "pending",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if department:
        row["department"] = department
    if token_secret_name:
        row["token_secret_name"] = token_secret_name
    if mcp_server_config:
        row["mcp_server_config"] = mcp_server_config
    if instance_url:
        row["instance_url"] = instance_url
    if scopes:
        row["scopes"] = scopes

    try:
        resp = client.table("company_saas_connections").insert(row).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        logger.exception("SaaS接続作成に失敗")
        return None


def get_connections(company_id: str) -> list[dict]:
    """企業のSaaS接続一覧を取得する."""
    client = _get_client()
    if not client:
        return []

    try:
        resp = (
            client.table("company_saas_connections")
            .select("*")
            .eq("company_id", company_id)
            .order("connected_at", desc=True)
            .execute()
        )
        return list(resp.data or [])
    except Exception:
        logger.exception("SaaS接続一覧取得に失敗")
        return []


def get_connection(connection_id: str, company_id: Optional[str] = None) -> dict | None:
    """SaaS接続を1件取得する."""
    client = _get_client()
    if not client:
        return None

    try:
        q = client.table("company_saas_connections").select("*").eq("id", connection_id)
        if company_id:
            q = q.eq("company_id", company_id)
        resp = q.execute()
        return resp.data[0] if resp.data else None
    except Exception:
        logger.exception("SaaS接続取得に失敗")
        return None


def get_connection_by_saas(
    company_id: str, saas_name: str, department: Optional[str] = None
) -> dict | None:
    """企業×SaaS名で接続を取得する."""
    client = _get_client()
    if not client:
        return None

    try:
        q = (
            client.table("company_saas_connections")
            .select("*")
            .eq("company_id", company_id)
            .eq("saas_name", saas_name)
        )
        if department:
            q = q.eq("department", department)
        q = q.order("updated_at", desc=True).limit(1)
        resp = q.execute()
        return resp.data[0] if resp.data else None
    except Exception:
        logger.exception("SaaS接続取得に失敗")
        return None


def update_connection(connection_id: str, updates: dict[str, Any]) -> dict | None:
    """SaaS接続を更新する."""
    client = _get_client()
    if not client:
        return None

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = (
            client.table("company_saas_connections")
            .update(updates)
            .eq("id", connection_id)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception:
        logger.exception("SaaS接続更新に失敗")
        return None


def update_status(connection_id: str, status: str, error_message: Optional[str] = None) -> None:
    """接続ステータスを更新する."""
    updates: dict[str, Any] = {"status": status}
    if error_message:
        updates["error_message"] = error_message
    if status == "active":
        updates["error_message"] = None
        updates["connected_at"] = datetime.now(timezone.utc).isoformat()
    update_connection(connection_id, updates)


def update_last_used(connection_id: str) -> None:
    """最終使用日時を更新する."""
    update_connection(connection_id, {"last_used_at": datetime.now(timezone.utc).isoformat()})


def update_health_check(connection_id: str, is_healthy: bool) -> None:
    """ヘルスチェック結果を更新する."""
    updates: dict[str, Any] = {
        "last_health_check_at": datetime.now(timezone.utc).isoformat(),
    }
    if not is_healthy:
        updates["status"] = "token_expired"
    update_connection(connection_id, updates)


def delete_connection(connection_id: str, company_id: Optional[str] = None) -> bool:
    """SaaS接続を削除する."""
    client = _get_client()
    if not client:
        return False

    try:
        q = client.table("company_saas_connections").delete().eq("id", connection_id)
        if company_id:
            q = q.eq("company_id", company_id)
        q.execute()
        return True
    except Exception:
        logger.exception("SaaS接続削除に失敗")
        return False


def get_active_connections_for_refresh() -> list[dict]:
    """トークンリフレッシュが必要なアクティブ接続を取得する."""
    client = _get_client()
    if not client:
        return []

    try:
        resp = (
            client.table("company_saas_connections")
            .select("*")
            .eq("status", "active")
            .eq("auth_method", "oauth2")
            .execute()
        )
        return list(resp.data or [])
    except Exception:
        logger.exception("リフレッシュ対象接続取得に失敗")
        return []

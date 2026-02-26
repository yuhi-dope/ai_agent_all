"""
OAuth トークンの CRUD 操作（Supabase バックエンド）。
persist.py と同じ _get_client() パターンに従う。
SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定時は何もしない。
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _get_client():
    """Supabase クライアントを返す（シングルトン）。"""
    from server._supabase import get_client

    return get_client()


def save_token(
    provider: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    scopes: Optional[str] = None,
    raw_response: Optional[dict] = None,
    tenant_id: str = "default",
) -> bool:
    """OAuth トークンを upsert する。成功時 True。"""
    client = _get_client()
    if not client:
        return False
    row = {
        "provider": provider,
        "tenant_id": tenant_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat() if expires_at else None,
        "scopes": scopes,
        "raw_response": raw_response,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table("oauth_tokens").upsert(
            row, on_conflict="provider,tenant_id"
        ).execute()
        return True
    except Exception as e:
        logger.warning("Failed to save OAuth token for %s: %s", provider, e)
        return False


def get_token(provider: str, tenant_id: str = "default") -> Optional[dict]:
    """指定プロバイダのトークンを取得する。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = (
            client.table("oauth_tokens")
            .select("*")
            .eq("provider", provider)
            .eq("tenant_id", tenant_id)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_tokens_bulk(
    providers: list[str], tenant_ids: list[str]
) -> dict[tuple[str, str], dict]:
    """複数プロバイダ・テナントのトークンを 1 クエリで取得する。
    戻り値: {(provider, tenant_id): token_row, ...}
    """
    client = _get_client()
    if not client:
        return {}
    try:
        r = (
            client.table("oauth_tokens")
            .select("*")
            .in_("provider", providers)
            .in_("tenant_id", tenant_ids)
            .execute()
        )
        result: dict[tuple[str, str], dict] = {}
        for row in r.data or []:
            result[(row["provider"], row["tenant_id"])] = row
        return result
    except Exception:
        return {}


def delete_token(provider: str, tenant_id: str = "default") -> bool:
    """指定プロバイダのトークンを削除する。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("oauth_tokens").delete().eq("provider", provider).eq(
            "tenant_id", tenant_id
        ).execute()
        return True
    except Exception:
        return False


def is_token_expired(token_data: dict) -> bool:
    """トークンの有効期限を5分バッファ付きでチェックする。期限なしは False。"""
    expires_at = token_data.get("expires_at")
    if not expires_at:
        return False
    if isinstance(expires_at, str):
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    else:
        exp = expires_at
    return datetime.now(timezone.utc) >= (exp - timedelta(minutes=5))

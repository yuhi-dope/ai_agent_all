"""kintone tool_connections から認証情報を解決する。"""
from __future__ import annotations

from typing import Any

from db.supabase import get_service_client
from security.encryption import decrypt_field


def resolve_kintone_credentials(company_id: str) -> dict[str, str]:
    db = get_service_client()
    result = (
        db.table("tool_connections")
        .select("connection_config")
        .eq("company_id", company_id)
        .eq("tool_name", "kintone")
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise RuntimeError(
            "kintone の接続設定がありません。設定 → 外部ツール連携で kintone を登録してください。"
        )
    cfg = result.data[0].get("connection_config") or {}
    enc = cfg.get("_encrypted")
    if not enc:
        raise RuntimeError("kintone の認証情報が暗号化形式ではありません。")
    creds: Any = decrypt_field(enc)
    if not isinstance(creds, dict):
        raise RuntimeError("kintone 認証情報の形式が不正です。")
    sub = creds.get("subdomain")
    tok = creds.get("api_token")
    if not sub or not tok:
        raise RuntimeError("kintone の subdomain / api_token が不足しています。")
    return {"subdomain": str(sub), "api_token": str(tok)}

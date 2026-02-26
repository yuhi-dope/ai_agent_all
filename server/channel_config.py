"""
チャネル連携のクライアント個別設定 CRUD。
channel_configs テーブルに Fernet 暗号化 JSON として保存する。
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# チャネル / SaaS ごとに許可されるフィールド
CHANNEL_FIELDS: dict[str, list[str]] = {
    # 通知チャネル
    "slack": ["client_id", "client_secret", "signing_secret"],
    "notion": ["client_id", "client_secret", "webhook_secret"],
    "gdrive": ["client_id", "client_secret", "watch_folder_id"],
    "chatwork": ["api_token", "webhook_token", "bot_account_id"],
    # SaaS BPO（AI社員用）
    "freee": ["client_id", "client_secret"],
    "salesforce": ["client_id", "client_secret"],
    "kintone": ["client_id", "client_secret"],
    "smarthr": ["client_id", "client_secret"],
    "google_workspace": ["client_id", "client_secret"],
}


def _get_client():
    from server._supabase import get_client
    return get_client()


def save_config(company_id: str, channel: str, config: dict) -> bool:
    """チャネル設定を暗号化して upsert する。"""
    if channel not in CHANNEL_FIELDS:
        return False
    client = _get_client()
    if not client:
        return False

    # 許可フィールドのみ抽出
    allowed = CHANNEL_FIELDS[channel]
    filtered = {k: v for k, v in config.items() if k in allowed and v}

    from server.crypto import encrypt
    config_enc = encrypt(json.dumps(filtered, ensure_ascii=False))

    row = {
        "company_id": company_id,
        "channel": channel,
        "config_enc": config_enc,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        client.table("channel_configs").upsert(
            row, on_conflict="company_id,channel"
        ).execute()
        return True
    except Exception as e:
        logger.warning("Failed to save channel config: %s", e)
        return False


def get_config(company_id: str, channel: str) -> dict | None:
    """チャネル設定を復号して返す。未設定なら None。"""
    client = _get_client()
    if not client:
        return None
    try:
        resp = (
            client.table("channel_configs")
            .select("config_enc")
            .eq("company_id", company_id)
            .eq("channel", channel)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        from server.crypto import decrypt
        return json.loads(decrypt(resp.data[0]["config_enc"]))
    except Exception as e:
        logger.warning("Failed to get channel config: %s", e)
        return None


def delete_config(company_id: str, channel: str) -> bool:
    """チャネル設定を削除する。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("channel_configs").delete().eq(
            "company_id", company_id
        ).eq("channel", channel).execute()
        return True
    except Exception as e:
        logger.warning("Failed to delete channel config: %s", e)
        return False


def get_all_configs(company_id: str) -> dict[str, dict]:
    """全チャネルの設定を返す。{channel: config_dict}"""
    client = _get_client()
    if not client:
        return {}
    try:
        resp = (
            client.table("channel_configs")
            .select("channel, config_enc")
            .eq("company_id", company_id)
            .execute()
        )
        from server.crypto import decrypt
        result = {}
        for row in resp.data or []:
            try:
                result[row["channel"]] = json.loads(decrypt(row["config_enc"]))
            except Exception:
                pass
        return result
    except Exception as e:
        logger.warning("Failed to get all channel configs: %s", e)
        return {}


def get_config_value(
    company_id: str | None,
    channel: str,
    field: str,
) -> str:
    """DB のテナント設定からフィールドを取得する。未設定なら空文字。"""
    if not company_id:
        return ""
    config = get_config(company_id, channel)
    if config and config.get(field):
        return config[field]
    return ""


def get_redirect_uri(channel: str) -> str:
    """BASE_URL からチャネル別 OAuth redirect_uri を自動生成する。"""
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base_url}/api/oauth/{channel}/callback"


def get_masked_config(company_id: str, channel: str) -> dict | None:
    """設定のマスク済み版を返す（UI 表示用）。"""
    config = get_config(company_id, channel)
    if not config:
        return None
    masked = {}
    for key, val in config.items():
        if not val:
            masked[key] = ""
        elif len(val) <= 8:
            masked[key] = "****"
        else:
            masked[key] = val[:4] + "..." + val[-4:]
    return masked

"""
Supabase クライアントのシングルトン。
各モジュールで _get_client() を毎回呼ぶと create_client() が都度実行され遅い。
このモジュールで 1 回だけ生成しキャッシュする。
"""

import os

_client = None


def get_client():
    """Supabase クライアントを返す。未設定時は None。"""
    global _client
    if _client is not None:
        return _client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or ""
    ).strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        _client = create_client(url, key)
        return _client
    except Exception:
        return None

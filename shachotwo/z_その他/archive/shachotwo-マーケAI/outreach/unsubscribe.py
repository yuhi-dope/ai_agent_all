"""配信停止管理（特定電子メール法準拠 — 即時反映）"""

from __future__ import annotations

# TODO: Supabase の apo_unsubscribes テーブルを使用
_unsubscribed: set[str] = set()


def add_unsubscribe(company_id: str) -> None:
    """配信停止を記録"""
    _unsubscribed.add(company_id)
    # TODO: INSERT INTO apo_unsubscribes


def is_unsubscribed(company_id: str) -> bool:
    """配信停止済みか確認"""
    return company_id in _unsubscribed
    # TODO: SELECT FROM apo_unsubscribes WHERE company_id = ...

"""LP閲覧トラッキング & シグナル温度判定"""

from __future__ import annotations


async def record_page_view(company_id: str, page_url: str, duration_sec: int) -> None:
    """LP閲覧ログを記録"""
    # TODO: apo_page_views に INSERT (Supabase)
    pass


def classify_signal(duration_sec: int) -> str:
    """滞在時間からシグナル温度を判定

    - 30秒以上 → WARM（翌日フォロー）
    - 3秒以下  → COLD（1週間後リトライ）
    - それ以外  → NEUTRAL
    """
    if duration_sec >= 30:
        return "warm"
    if duration_sec <= 3:
        return "cold"
    return "neutral"

"""ナレッジ鮮度管理エンジン。

更新日時ベースで古くなったナレッジを検出し、
リフレッシュ提案を生成する。
"""
import json
import logging
from datetime import datetime, timezone

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# 鮮度しきい値（日数）— カテゴリ別
FRESHNESS_THRESHOLDS: dict[str, int] = {
    "pricing": 90,         # 価格は3ヶ月で要確認
    "compliance": 180,     # コンプライアンスは半年
    "hr": 180,             # 人事・労務は半年
    "workflow": 365,       # 業務フローは1年
    "safety": 90,          # 安全管理は3ヶ月
    "finance": 90,         # 経理は3ヶ月
    "__default__": 365,    # デフォルト1年
}


async def detect_stale_knowledge(
    company_id: str,
    department: str | None = None,
) -> list[dict]:
    """鮮度切れナレッジを検出。

    Args:
        company_id: テナントID（RLS用）
        department: 部署名フィルタ（省略時は全部署）

    Returns:
        list of {"item": {...}, "days_since_update": int, "threshold_days": int, "staleness_score": float}
        staleness_score は threshold_days に対する超過倍率（1.0 = ちょうど閾値、3.0 = 上限キャップ）
    """
    db = get_service_client()

    q = db.table("knowledge_items") \
        .select("id, title, department, category, item_type, confidence, updated_at, content") \
        .eq("company_id", company_id) \
        .eq("is_active", True) \
        .order("updated_at", desc=False) \
        .limit(200)

    if department:
        q = q.eq("department", department)

    result = q.execute()
    items = result.data or []

    now = datetime.now(timezone.utc)
    stale_items = []

    for item in items:
        try:
            updated = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            logger.warning("Invalid updated_at for knowledge_item %s", item.get("id"))
            continue

        days = (now - updated).days
        threshold = FRESHNESS_THRESHOLDS.get(
            item["category"],
            FRESHNESS_THRESHOLDS["__default__"],
        )

        if days >= threshold:
            staleness = min(days / threshold, 3.0)  # 最大3倍超過
            stale_items.append({
                "item": {
                    "id": item["id"],
                    "title": item["title"],
                    "department": item["department"],
                    "category": item["category"],
                    "content_preview": item["content"][:200] if item.get("content") else "",
                },
                "days_since_update": days,
                "threshold_days": threshold,
                "staleness_score": round(staleness, 2),
            })

    # 鮮度スコア降順でソート（古いほど上位）
    stale_items.sort(key=lambda x: x["staleness_score"], reverse=True)

    # 鮮度切れをproactive_proposalsに登録
    if stale_items:
        _create_freshness_proposals(db, company_id, stale_items)

    return stale_items


def _create_freshness_proposals(
    db,
    company_id: str,
    stale_items: list[dict],
) -> None:
    """鮮度切れナレッジの更新提案をDBに保存。

    同一ナレッジに対する未処理の鮮度提案が既存する場合はスキップ。
    上位10件まで個別提案を作成する。
    """
    for item_info in stale_items[:10]:
        item = item_info["item"]
        days = item_info["days_since_update"]
        threshold = item_info["threshold_days"]

        # 同じナレッジに対する未処理の鮮度提案があればスキップ
        # UUID[]配列の要素検索はcontains()が不確実なためPythonでフィルタ
        existing = db.table("proactive_proposals") \
            .select("id, related_knowledge_ids") \
            .eq("company_id", company_id) \
            .eq("proposal_type", "rule_challenge") \
            .eq("status", "proposed") \
            .execute()

        has_duplicate = any(
            item["id"] in (row.get("related_knowledge_ids") or [])
            for row in (existing.data or [])
        )
        if has_duplicate:
            continue

        db.table("proactive_proposals").insert({
            "company_id": company_id,
            "proposal_type": "rule_challenge",
            "title": f"ナレッジの鮮度切れ: {item['title']}",
            "description": (
                f"「{item['title']}」は最終更新から **{days}日** 経過しています"
                f"（推奨更新期間: {threshold}日）。\n\n"
                f"**カテゴリ**: {item['category']}\n"
                f"**部署**: {item['department']}\n\n"
                f"内容が最新の状況と一致しているか確認してください。"
            ),
            "impact_estimate": json.dumps({
                "risk_reduction": min(item_info["staleness_score"] * 0.3, 0.9),
                "confidence": 0.8,
                "calculation_basis": f"最終更新から{days}日経過（閾値{threshold}日）",
            }),
            "evidence": json.dumps({
                "signals": [
                    {
                        "source": "knowledge",
                        "value": f"最終更新: {days}日前",
                        "score": min(item_info["staleness_score"] / 3, 1.0),
                    },
                ]
            }),
            "related_knowledge_ids": [item["id"]],
            "status": "proposed",
        }).execute()

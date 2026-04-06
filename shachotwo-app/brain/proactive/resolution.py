"""提案の承認・却下・解決フロー（Human-in-the-Loop）。

proactive_proposalsのステータス管理:
  proposed → reviewed（管理者確認済み）
  reviewed → accepted（承認）→ implemented（自動適用済み）
  reviewed → rejected（却下）

矛盾解決時: 古い方のナレッジをis_active=False + superseded_by設定
鮮度切れ解決時: ナレッジのupdated_at更新確認を促す
"""
import logging
from datetime import datetime, timezone

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


async def review_proposal(
    company_id: str,
    proposal_id: str,
    reviewer_id: str,
) -> dict:
    """提案をレビュー済みにマーク。

    Args:
        company_id: テナントID（RLS用）
        proposal_id: 対象の提案ID
        reviewer_id: レビューしたユーザーID

    Returns:
        {"status": "reviewed", "proposal_id": str}
    """
    db = get_service_client()
    db.table("proactive_proposals") \
        .update({"status": "reviewed", "reviewed_by": reviewer_id}) \
        .eq("id", proposal_id) \
        .eq("company_id", company_id) \
        .execute()
    return {"status": "reviewed", "proposal_id": proposal_id}


async def accept_proposal(
    company_id: str,
    proposal_id: str,
    reviewer_id: str,
    resolution_action: str | None = None,
) -> dict:
    """提案を承認し、可能な場合は自動適用。

    Args:
        company_id: テナントID（RLS用）
        proposal_id: 対象の提案ID
        reviewer_id: 承認したユーザーID
        resolution_action: 自動適用アクション。
            "deactivate_a" — related_knowledge_ids[0]を無効化し[1]で置き換え
            "deactivate_b" — related_knowledge_ids[1]を無効化し[0]で置き換え
            "refresh"      — related_knowledge_ids[0]のupdated_atを現在時刻に更新
            None           — 自動適用なし（accepted止まり）

    Returns:
        {"status": "implemented"|"accepted"|"error", "proposal_id": str}
    """
    db = get_service_client()

    # 提案を取得
    try:
        prop_result = db.table("proactive_proposals") \
            .select("*") \
            .eq("id", proposal_id) \
            .eq("company_id", company_id) \
            .single() \
            .execute()
        proposal = prop_result.data
    except Exception as e:
        logger.error("Failed to fetch proposal %s: %s", proposal_id, e)
        return {"error": "proposal_not_found"}

    if not proposal:
        return {"error": "proposal_not_found"}

    # ステータス更新
    db.table("proactive_proposals") \
        .update({"status": "accepted", "reviewed_by": reviewer_id}) \
        .eq("id", proposal_id) \
        .eq("company_id", company_id) \
        .execute()

    # 自動適用（矛盾解決 / 鮮度リフレッシュ）
    if proposal["proposal_type"] == "rule_challenge" and resolution_action:
        applied = await _apply_resolution(
            db, company_id, proposal, resolution_action
        )
        if applied:
            db.table("proactive_proposals") \
                .update({"status": "implemented"}) \
                .eq("id", proposal_id) \
                .eq("company_id", company_id) \
                .execute()
            return {
                "status": "implemented",
                "proposal_id": proposal_id,
                "action": resolution_action,
            }

    return {"status": "accepted", "proposal_id": proposal_id}


async def reject_proposal(
    company_id: str,
    proposal_id: str,
    reviewer_id: str,
    reason: str | None = None,
) -> dict:
    """提案を却下。

    Args:
        company_id: テナントID（RLS用）
        proposal_id: 対象の提案ID
        reviewer_id: 却下したユーザーID
        reason: 却下理由（任意、ログのみ）

    Returns:
        {"status": "rejected", "proposal_id": str}
    """
    db = get_service_client()
    db.table("proactive_proposals") \
        .update({"status": "rejected", "reviewed_by": reviewer_id}) \
        .eq("id", proposal_id) \
        .eq("company_id", company_id) \
        .execute()

    if reason:
        logger.info("Proposal %s rejected by %s: %s", proposal_id, reviewer_id, reason)

    return {"status": "rejected", "proposal_id": proposal_id}


async def get_pending_proposals(
    company_id: str,
    proposal_type: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """未処理の提案を取得（管理画面用）。

    status が "proposed" または "reviewed" の提案を返す。
    created_at 降順（新しい順）。

    Args:
        company_id: テナントID（RLS用）
        proposal_type: 提案種別フィルタ（省略時は全種別）
        limit: 取得件数上限（デフォルト20）

    Returns:
        list of proactive_proposals rows
    """
    db = get_service_client()
    q = db.table("proactive_proposals") \
        .select("*") \
        .eq("company_id", company_id) \
        .in_("status", ["proposed", "reviewed"]) \
        .order("created_at", desc=True) \
        .limit(limit)

    if proposal_type:
        q = q.eq("proposal_type", proposal_type)

    result = q.execute()
    return result.data or []


async def _apply_resolution(
    db,
    company_id: str,
    proposal: dict,
    action: str,
) -> bool:
    """矛盾・鮮度切れの自動解決を適用。

    Args:
        db: Supabase service client
        company_id: テナントID（RLS用）
        proposal: proactive_proposals の行データ
        action: "deactivate_a" | "deactivate_b" | "refresh"

    Returns:
        True if applied successfully, False otherwise
    """
    knowledge_ids: list[str] = proposal.get("related_knowledge_ids") or []

    if action == "deactivate_a" and len(knowledge_ids) >= 2:
        # ナレッジAを無効化し、BでsupersededByを設定
        db.table("knowledge_items") \
            .update({"is_active": False, "superseded_by": knowledge_ids[1]}) \
            .eq("id", knowledge_ids[0]) \
            .eq("company_id", company_id) \
            .execute()
        logger.info(
            "Deactivated knowledge %s, superseded by %s",
            knowledge_ids[0], knowledge_ids[1],
        )
        return True

    elif action == "deactivate_b" and len(knowledge_ids) >= 2:
        db.table("knowledge_items") \
            .update({"is_active": False, "superseded_by": knowledge_ids[0]}) \
            .eq("id", knowledge_ids[1]) \
            .eq("company_id", company_id) \
            .execute()
        logger.info(
            "Deactivated knowledge %s, superseded by %s",
            knowledge_ids[1], knowledge_ids[0],
        )
        return True

    elif action == "refresh" and len(knowledge_ids) >= 1:
        # 鮮度リフレッシュ: updated_atを現在に更新（内容確認済みを意味する）
        db.table("knowledge_items") \
            .update({"updated_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", knowledge_ids[0]) \
            .eq("company_id", company_id) \
            .execute()
        logger.info("Refreshed knowledge %s timestamp", knowledge_ids[0])
        return True

    logger.warning(
        "Unknown resolution action '%s' or insufficient knowledge_ids (%d)",
        action, len(knowledge_ids),
    )
    return False

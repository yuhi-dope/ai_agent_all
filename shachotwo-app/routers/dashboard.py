"""ダッシュボード集計エンドポイント"""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RecentKnowledgeItem(BaseModel):
    """最近のナレッジアイテム概要"""
    id: UUID
    title: str
    department: str
    created_at: datetime


class RecentProposal(BaseModel):
    """最近の提案概要"""
    id: UUID
    title: str
    type: str
    priority: str
    status: str
    created_at: datetime


class DashboardSummaryResponse(BaseModel):
    """ダッシュボード集計レスポンス"""
    knowledge_count: int
    proposal_count: int
    recent_knowledge: list[RecentKnowledgeItem]
    recent_proposals: list[RecentProposal]
    snapshot_date: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    user: JWTClaims = Depends(get_current_user),
):
    """ダッシュボード集計 — ナレッジ数・提案数・最新アイテム・スナップショット日時"""
    db = get_service_client()
    company_id = user.company_id

    try:
        # ナレッジ総数（アクティブのみ）
        knowledge_count_result = db.table("knowledge_items") \
            .select("id", count="exact") \
            .eq("company_id", company_id) \
            .eq("is_active", True) \
            .execute()
        knowledge_count = knowledge_count_result.count or 0

        # 提案数（pending / proposed ステータス）
        proposal_count_result = db.table("proactive_proposals") \
            .select("id", count="exact") \
            .eq("company_id", company_id) \
            .in_("status", ["pending", "proposed"]) \
            .execute()
        proposal_count = proposal_count_result.count or 0

        # 最新ナレッジ5件
        recent_knowledge_result = db.table("knowledge_items") \
            .select("id, title, department, created_at") \
            .eq("company_id", company_id) \
            .eq("is_active", True) \
            .order("created_at", desc=True) \
            .limit(5) \
            .execute()

        # 最新提案3件
        recent_proposals_result = db.table("proactive_proposals") \
            .select("id, title, type, priority, status, created_at") \
            .eq("company_id", company_id) \
            .order("created_at", desc=True) \
            .limit(3) \
            .execute()

        # 最新スナップショット日時
        snapshot_result = db.table("company_state_snapshots") \
            .select("snapshot_at") \
            .eq("company_id", company_id) \
            .order("snapshot_at", desc=True) \
            .limit(1) \
            .execute()
        snapshot_date = (
            snapshot_result.data[0]["snapshot_at"]
            if snapshot_result.data
            else None
        )

    except Exception as e:
        logger.error(f"Dashboard summary query failed: {e}")
        raise HTTPException(status_code=500, detail="ダッシュボード集計の取得に失敗しました")

    return DashboardSummaryResponse(
        knowledge_count=knowledge_count,
        proposal_count=proposal_count,
        recent_knowledge=[
            RecentKnowledgeItem(**item)
            for item in recent_knowledge_result.data
        ],
        recent_proposals=[
            RecentProposal(**item)
            for item in recent_proposals_result.data
        ],
        snapshot_date=snapshot_date,
    )

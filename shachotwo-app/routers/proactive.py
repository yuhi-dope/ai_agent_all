"""Proactive proposal endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.proactive import analyze_and_propose
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class ProposalResponse(BaseModel):
    id: UUID
    proposal_type: str
    title: str
    description: str
    impact_estimate: Optional[dict] = None
    evidence: Optional[dict] = None
    priority: str = "medium"
    status: str
    created_at: datetime


class ProposalUpdate(BaseModel):
    status: str  # "accepted" | "rejected"
    review_note: Optional[str] = None


class ProposalListResponse(BaseModel):
    items: list[ProposalResponse]
    total: int
    has_more: bool = False


class AnalyzeRequest(BaseModel):
    department: Optional[str] = None


class AnalyzeResponse(BaseModel):
    proposals_created: int
    model_used: str
    knowledge_analyzed: int


@router.post("/proactive/analyze", response_model=AnalyzeResponse)
async def trigger_analysis(
    request: Request,
    body: AnalyzeRequest = AnalyzeRequest(),
    user: JWTClaims = Depends(require_role("admin")),
):
    """能動提案分析を実行（admin のみ）"""
    try:
        result = await analyze_and_propose(
            company_id=user.company_id,
            department=body.department,
        )
        response = AnalyzeResponse(
            proposals_created=len(result.proposals),
            model_used=result.model_used,
            knowledge_analyzed=result.knowledge_count,
        )
        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="proactive_analysis",
            details={"proposals_created": len(result.proposals), "knowledge_analyzed": result.knowledge_count},
            ip_address=request.client.host if request.client else None,
        )
        return response
    except Exception as e:
        logger.error(f"Proactive analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/proactive/proposals", response_model=ProposalListResponse)
async def list_proposals(
    proposal_status: Optional[str] = Query(None, alias="status"),
    proposal_type: Optional[str] = Query(None, alias="type"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """能動提案一覧"""
    db = get_service_client()
    q = db.table("proactive_proposals") \
        .select("*", count="exact") \
        .eq("company_id", user.company_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1)

    if proposal_status:
        q = q.eq("status", proposal_status)
    if proposal_type:
        q = q.eq("proposal_type", proposal_type)

    result = q.execute()
    return ProposalListResponse(
        items=[ProposalResponse(**{**r, "priority": r.get("priority", "medium")}) for r in result.data],
        total=result.count or 0,
        has_more=(offset + limit) < (result.count or 0),
    )


@router.patch("/proactive/proposals/{proposal_id}", response_model=ProposalResponse)
async def review_proposal(
    proposal_id: UUID,
    body: ProposalUpdate,
    user: JWTClaims = Depends(require_role("admin")),
):
    """提案レビュー（admin のみ）"""
    if body.status not in ("accepted", "rejected", "reviewed", "implemented"):
        raise HTTPException(status_code=400, detail="Invalid status")

    db = get_service_client()
    result = db.table("proactive_proposals") \
        .update({
            "status": body.status,
            "reviewed_by": user.sub,
        }) \
        .eq("id", str(proposal_id)) \
        .eq("company_id", user.company_id) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Proposal not found")

    return ProposalResponse(**result.data[0])

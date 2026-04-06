"""Proactive proposal endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.proactive import (
    analyze_and_propose,
    detect_contradictions,
    detect_stale_knowledge,
    accept_proposal as brain_accept_proposal,
    reject_proposal as brain_reject_proposal,
    review_proposal as brain_review_proposal,
    get_pending_proposals,
)
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


class ContradictionResponse(BaseModel):
    contradictions_found: int
    details: list[dict]


class FreshnessResponse(BaseModel):
    stale_items_found: int
    details: list[dict]


class ResolutionRequest(BaseModel):
    status: str  # "reviewed" | "accepted" | "rejected"
    resolution_action: Optional[str] = None  # "deactivate_a" | "deactivate_b" | "refresh"
    reason: Optional[str] = None


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


@router.post("/proactive/contradictions", response_model=ContradictionResponse)
async def trigger_contradiction_check(
    request: Request,
    body: AnalyzeRequest = AnalyzeRequest(),
    user: JWTClaims = Depends(require_role("admin")),
):
    """ナレッジ矛盾検知を実行（admin のみ）"""
    try:
        results = await detect_contradictions(
            company_id=user.company_id,
            department=body.department,
        )
        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="contradiction_check",
            details={"contradictions_found": len(results)},
            ip_address=request.client.host if request.client else None,
        )
        return ContradictionResponse(
            contradictions_found=len(results),
            details=results,
        )
    except Exception as e:
        logger.error("Contradiction check failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/proactive/freshness", response_model=FreshnessResponse)
async def trigger_freshness_check(
    request: Request,
    body: AnalyzeRequest = AnalyzeRequest(),
    user: JWTClaims = Depends(require_role("admin")),
):
    """ナレッジ鮮度チェックを実行（admin のみ）"""
    try:
        results = await detect_stale_knowledge(
            company_id=user.company_id,
            department=body.department,
        )
        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="freshness_check",
            details={"stale_items_found": len(results)},
            ip_address=request.client.host if request.client else None,
        )
        return FreshnessResponse(
            stale_items_found=len(results),
            details=results,
        )
    except Exception as e:
        logger.error("Freshness check failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/proactive/proposals/{proposal_id}")
async def resolve_proposal(
    proposal_id: UUID,
    body: ResolutionRequest,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """提案の承認・却下・解決（admin のみ）

    resolution_action を指定すると矛盾解決・鮮度リフレッシュを自動適用。
    """
    pid = str(proposal_id)

    if body.status == "reviewed":
        result = await brain_review_proposal(user.company_id, pid, user.sub)
    elif body.status == "accepted":
        result = await brain_accept_proposal(
            user.company_id, pid, user.sub, body.resolution_action,
        )
    elif body.status == "rejected":
        result = await brain_reject_proposal(
            user.company_id, pid, user.sub, body.reason,
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid status. Use: reviewed, accepted, rejected")

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="update",
        resource_type="proactive_proposal",
        resource_id=pid,
        details={"new_status": result["status"], "action": body.resolution_action},
        ip_address=request.client.host if request.client else None,
    )

    # 更新後のデータを返す
    db = get_service_client()
    updated = db.table("proactive_proposals") \
        .select("*") \
        .eq("id", pid) \
        .eq("company_id", user.company_id) \
        .execute()

    if not updated.data:
        raise HTTPException(status_code=404, detail="Proposal not found")

    return ProposalResponse(**{**updated.data[0], "priority": updated.data[0].get("priority", "medium")})

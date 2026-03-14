"""Knowledge Q&A and CRUD endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.knowledge.qa import answer_question
from brain.knowledge.embeddings import update_item_embedding
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class QARequest(BaseModel):
    question: str
    department: Optional[str] = None
    top_k: int = 5


class SourceCitation(BaseModel):
    knowledge_id: UUID
    title: str
    relevance: float
    excerpt: Optional[str] = None


class QAResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]
    confidence: float


class KnowledgeItemResponse(BaseModel):
    id: UUID
    department: str
    category: str
    item_type: str
    title: str
    content: str
    conditions: Optional[list | dict] = None
    examples: Optional[list | dict] = None
    exceptions: Optional[list | dict] = None
    source_type: str
    confidence: Optional[float] = None
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    conditions: Optional[list | dict] = None
    is_active: Optional[bool] = None
    version: int  # required for optimistic locking


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeItemResponse]
    total: int
    has_more: bool = False


@router.post("/knowledge/ask", response_model=QAResponse)
async def ask_question_endpoint(
    body: QARequest,
    user: JWTClaims = Depends(get_current_user),
):
    """Q&A — ナレッジベース検索 + LLM回答生成"""
    try:
        result = await answer_question(
            question=body.question,
            company_id=user.company_id,
            department=body.department,
            top_k=body.top_k,
        )
        return QAResponse(
            answer=result.answer,
            sources=[
                SourceCitation(
                    knowledge_id=s.knowledge_id,
                    title=s.title,
                    relevance=s.relevance,
                    excerpt=s.excerpt,
                )
                for s in result.sources
            ],
            confidence=result.confidence,
        )
    except Exception as e:
        logger.error(f"Q&A failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/items", response_model=KnowledgeListResponse)
async def list_knowledge_items(
    department: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    is_active: bool = True,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """ナレッジ一覧"""
    db = get_service_client()
    q = db.table("knowledge_items") \
        .select("id, department, category, item_type, title, content, conditions, examples, exceptions, source_type, confidence, version, is_active, created_at, updated_at", count="exact") \
        .eq("company_id", user.company_id) \
        .eq("is_active", is_active) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1)

    if department:
        q = q.eq("department", department)
    if category:
        q = q.eq("category", category)
    if search:
        q = q.or_(f"title.ilike.%{search}%,content.ilike.%{search}%")

    result = q.execute()
    return KnowledgeListResponse(
        items=[KnowledgeItemResponse(**r) for r in result.data],
        total=result.count or 0,
        has_more=(offset + limit) < (result.count or 0),
    )


@router.get("/knowledge/items/{item_id}", response_model=KnowledgeItemResponse)
async def get_knowledge_item(
    item_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """ナレッジ詳細"""
    db = get_service_client()
    result = db.table("knowledge_items") \
        .select("id, department, category, item_type, title, content, conditions, examples, exceptions, source_type, confidence, version, is_active, created_at, updated_at") \
        .eq("id", str(item_id)) \
        .eq("company_id", user.company_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    return KnowledgeItemResponse(**result.data)


@router.patch("/knowledge/items/{item_id}", response_model=KnowledgeItemResponse)
async def update_knowledge_item(
    item_id: UUID,
    body: KnowledgeItemUpdate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """ナレッジ更新（admin のみ, 楽観的ロック）"""
    db = get_service_client()

    # Fetch current version for optimistic locking
    current = db.table("knowledge_items") \
        .select("version") \
        .eq("id", str(item_id)) \
        .eq("company_id", user.company_id) \
        .single() \
        .execute()

    if not current.data:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    if current.data["version"] != body.version:
        raise HTTPException(
            status_code=409,
            detail="VERSION_CONFLICT: Item has been modified by another user. Please refresh and try again.",
        )

    # Build update payload
    update_data: dict = {"version": body.version + 1}
    if body.title is not None:
        update_data["title"] = body.title
    if body.content is not None:
        update_data["content"] = body.content
    if body.conditions is not None:
        update_data["conditions"] = body.conditions
    if body.is_active is not None:
        update_data["is_active"] = body.is_active

    result = db.table("knowledge_items") \
        .update(update_data) \
        .eq("id", str(item_id)) \
        .eq("company_id", user.company_id) \
        .execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Update failed")

    updated = result.data[0]
    updated.pop("embedding", None)

    # Re-generate embedding if content changed
    if body.title is not None or body.content is not None:
        try:
            await update_item_embedding(str(item_id), user.company_id)
        except Exception as e:
            logger.warning(f"Failed to update embedding for {item_id}: {e}")

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="update",
        resource_type="knowledge_item",
        resource_id=str(item_id),
        details={"new_values": {k: v for k, v in update_data.items() if k != "version"}},
        ip_address=request.client.host if request.client else None,
    )
    return KnowledgeItemResponse(**updated)

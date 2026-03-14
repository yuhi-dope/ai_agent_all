"""Knowledge ingestion endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.extraction import extract_knowledge
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class TextIngestionRequest(BaseModel):
    content: str
    department: Optional[str] = None
    category: Optional[str] = None


class SessionResponse(BaseModel):
    id: UUID
    input_type: str
    extraction_status: str
    created_at: datetime


class ExtractedItemBrief(BaseModel):
    title: str
    content: str
    category: str
    item_type: str
    department: str
    confidence: float


class TextIngestionResponse(BaseModel):
    session_id: UUID
    items: list[ExtractedItemBrief] = []
    model_used: str = ""
    cost_yen: float = 0.0


class FileIngestionResponse(BaseModel):
    session_id: UUID
    items: list[ExtractedItemBrief] = []
    model_used: str = ""
    cost_yen: float = 0.0
    file_type: str
    file_size: int


class SessionDetailResponse(BaseModel):
    id: UUID
    input_type: str
    extraction_status: str
    extraction_error: Optional[dict] = None
    knowledge_items: list = []
    created_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    has_more: bool = False


@router.post("/ingestion/text", response_model=TextIngestionResponse)
async def ingest_text(
    body: TextIngestionRequest,
    request: Request,
    user: JWTClaims = Depends(get_current_user),
):
    """テキストナレッジ入力 → LLM構造化 → knowledge_items保存"""
    try:
        result = await extract_knowledge(
            text=body.content,
            company_id=user.company_id,
            user_id=user.sub,
            department=body.department,
            category=body.category,
        )
        response = TextIngestionResponse(
            session_id=result.session_id,
            items=[
                ExtractedItemBrief(
                    title=item.title,
                    content=item.content,
                    category=item.category,
                    item_type=item.item_type,
                    department=item.department,
                    confidence=item.confidence,
                )
                for item in result.items
            ],
            model_used=result.model_used,
            cost_yen=result.cost_yen,
        )
        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="knowledge_session",
            resource_id=str(result.session_id),
            details={"input_type": "text", "items_extracted": len(result.items)},
            ip_address=request.client.host if request.client else None,
        )
        return response
    except Exception as e:
        logger.error(f"Text ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


@router.post("/ingestion/file", response_model=FileIngestionResponse)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    department: Optional[str] = None,
    category: Optional[str] = None,
    user: JWTClaims = Depends(get_current_user),
):
    """ファイルナレッジ入力（txt/PDF/Excel/Word） → テキスト抽出 → LLM構造化 → knowledge_items保存"""
    from brain.ingestion.file import ingest_file as do_ingest_file

    # Validate file extension
    filename = file.filename or "unknown"
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS.keys()))}",
        )

    # Read file content with size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large: {len(content)} bytes. Maximum: {MAX_FILE_SIZE} bytes (10MB).",
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    # Resolve content type from extension (more reliable than browser-sent type)
    content_type = ALLOWED_EXTENSIONS[ext]

    try:
        result = await do_ingest_file(
            file_content=content,
            filename=filename,
            content_type=content_type,
            company_id=user.company_id,
            user_id=user.sub,
            department=department,
            category=category,
        )

        response = FileIngestionResponse(
            session_id=result.session_id,
            items=[
                ExtractedItemBrief(
                    title=item.title,
                    content=item.content,
                    category=item.category,
                    item_type=item.item_type,
                    department=item.department,
                    confidence=item.confidence,
                )
                for item in result.items
            ],
            model_used=result.model_used,
            cost_yen=result.cost_yen,
            file_type=ext,
            file_size=len(content),
        )

        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="knowledge_session",
            resource_id=str(result.session_id),
            details={
                "input_type": "document",
                "file_name": filename,
                "file_type": ext,
                "file_size": len(content),
                "items_extracted": len(result.items),
            },
            ip_address=request.client.host if request.client else None,
        )

        return response

    except ValueError as e:
        logger.warning(f"File ingestion validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"File ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _get_extension(filename: str) -> str:
    """Extract lowercase file extension from filename."""
    import os
    _, ext = os.path.splitext(filename)
    return ext.lower()


@router.get("/ingestion/sessions", response_model=SessionListResponse)
async def list_sessions(
    extraction_status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """セッション一覧"""
    db = get_service_client()
    q = db.table("knowledge_sessions") \
        .select("id, input_type, extraction_status, created_at", count="exact") \
        .eq("company_id", user.company_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1)

    if extraction_status:
        q = q.eq("extraction_status", extraction_status)

    result = q.execute()
    return SessionListResponse(
        items=[SessionResponse(**r) for r in result.data],
        total=result.count or 0,
        has_more=(offset + limit) < (result.count or 0),
    )


@router.get("/ingestion/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """セッション詳細"""
    db = get_service_client()
    result = db.table("knowledge_sessions") \
        .select("*") \
        .eq("id", str(session_id)) \
        .eq("company_id", user.company_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch related knowledge items
    items = db.table("knowledge_items") \
        .select("id, title, item_type, department, confidence") \
        .eq("session_id", str(session_id)) \
        .execute()

    return SessionDetailResponse(
        id=result.data["id"],
        input_type=result.data["input_type"],
        extraction_status=result.data["extraction_status"],
        extraction_error=result.data.get("extraction_error"),
        knowledge_items=items.data or [],
        created_at=result.data["created_at"],
    )

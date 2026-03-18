"""Knowledge Q&A and CRUD endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
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
    missing_info: Optional[str] = None
    model_used: str = "gemini-2.5-flash"
    cost_yen: float = 0.0


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
    department: Optional[str] = None
    category: Optional[str] = None
    conditions: Optional[list | dict] = None
    examples: Optional[list | dict] = None
    exceptions: Optional[list | dict] = None
    is_active: Optional[bool] = None
    version: int  # required for optimistic locking


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeItemResponse]
    total: int
    has_more: bool = False


class FileInfoResponse(BaseModel):
    file_name: str
    file_size: int
    file_content_type: str
    download_url: str
    created_at: datetime


class FileInfoOptional(BaseModel):
    """ファイル情報（ない場合はnull）"""
    file: Optional[FileInfoResponse] = None


@router.post("/knowledge/ask", response_model=QAResponse)
async def ask_question_endpoint(
    body: QARequest,
    user: JWTClaims = Depends(get_current_user),
):
    """Q&A — ナレッジベース検索 + LLM回答生成 + 履歴保存"""
    try:
        result = await answer_question(
            question=body.question,
            company_id=user.company_id,
            department=body.department,
            top_k=body.top_k,
        )

        # Determine answer_status
        answer_status = "answered"
        if not result.sources:
            answer_status = "no_match"
        elif result.confidence < 0.5:
            answer_status = "partial"

        # Save to qa_sessions (fire-and-forget, don't block response)
        try:
            db = get_service_client()
            db.table("qa_sessions").insert({
                "company_id": str(user.company_id),
                "user_id": str(user.sub),
                "question": body.question,
                "answer": result.answer,
                "referenced_knowledge_ids": [str(s.knowledge_id) for s in result.sources],
                "answer_status": answer_status,
                "model_used": result.model_used,
                "confidence": result.confidence,
                "cost_yen": result.cost_yen,
            }).execute()
        except Exception as save_err:
            logger.warning(f"Failed to save qa_session: {save_err}")

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
            missing_info=result.missing_info,
            model_used=result.model_used,
            cost_yen=result.cost_yen,
        )
    except Exception as e:
        logger.error(f"Q&A failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/departments")
async def list_departments(
    user: JWTClaims = Depends(get_current_user),
) -> list[str]:
    """登録済みナレッジの部署一覧（アクティブのみ）"""
    db = get_service_client()
    result = db.table("knowledge_items") \
        .select("department") \
        .eq("company_id", user.company_id) \
        .eq("is_active", True) \
        .execute()

    depts = sorted({r["department"] for r in result.data if r.get("department")})
    return depts


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
    if body.department is not None:
        update_data["department"] = body.department
    if body.category is not None:
        update_data["category"] = body.category
    if body.conditions is not None:
        update_data["conditions"] = body.conditions
    if body.examples is not None:
        update_data["examples"] = body.examples
    if body.exceptions is not None:
        update_data["exceptions"] = body.exceptions
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


def _get_session_for_item(db, item_id: str, company_id: str) -> Optional[dict]:
    """knowledge_itemのsession_idからknowledge_sessionsを引く。"""
    item_result = db.table("knowledge_items") \
        .select("session_id") \
        .eq("id", item_id) \
        .eq("company_id", company_id) \
        .single() \
        .execute()

    if not item_result.data:
        return None

    session_id = item_result.data.get("session_id")
    if not session_id:
        return None

    session_result = db.table("knowledge_sessions") \
        .select("id, file_name, file_size, file_content_type, file_storage_path, created_at") \
        .eq("id", str(session_id)) \
        .eq("company_id", company_id) \
        .single() \
        .execute()

    return session_result.data if session_result.data else None


@router.get("/knowledge/items/{item_id}/file", response_model=FileInfoOptional)
async def get_knowledge_item_file(
    item_id: UUID,
    user: JWTClaims = Depends(get_current_user),
) -> FileInfoOptional:
    """ナレッジアイテムに紐づくソースファイル情報を返す。ファイルがない場合はfile=null。"""
    db = get_service_client()

    session = _get_session_for_item(db, str(item_id), str(user.company_id))
    if not session:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    storage_path: Optional[str] = session.get("file_storage_path")
    if not storage_path:
        return FileInfoOptional(file=None)

    # 署名付きURL発行（有効期限1時間）
    try:
        signed = db.storage.from_("knowledge-files").create_signed_url(storage_path, 3600)
        download_url: str = signed.get("signedURL") or signed.get("signed_url") or ""
        if not download_url:
            raise ValueError("No signed URL returned")
    except Exception as e:
        logger.error(f"Failed to create signed URL for {storage_path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    return FileInfoOptional(
        file=FileInfoResponse(
            file_name=session["file_name"],
            file_size=session["file_size"],
            file_content_type=session["file_content_type"],
            download_url=download_url,
            created_at=session["created_at"],
        )
    )


@router.delete("/knowledge/items/{item_id}/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_item_file(
    item_id: UUID,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
) -> None:
    """ソースファイルを削除する（admin only）。"""
    db = get_service_client()

    session = _get_session_for_item(db, str(item_id), str(user.company_id))
    if not session:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    storage_path: Optional[str] = session.get("file_storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="No file attached to this knowledge item")

    # Supabase Storageからファイル削除
    try:
        db.storage.from_("knowledge-files").remove([storage_path])
    except Exception as e:
        logger.error(f"Failed to delete file from Storage {storage_path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file from storage")

    # knowledge_sessionsのファイルメタデータをnullクリア
    try:
        db.table("knowledge_sessions").update({
            "file_name": None,
            "file_size": None,
            "file_content_type": None,
            "file_storage_path": None,
        }).eq("id", str(session["id"])).execute()
    except Exception as e:
        logger.error(f"Failed to clear file metadata for session {session['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear file metadata")

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="delete",
        resource_type="knowledge_item_file",
        resource_id=str(item_id),
        details={"storage_path": storage_path, "session_id": str(session["id"])},
        ip_address=request.client.host if request.client else None,
    )


# ingestion.py と共通のバリデーション定数（再定義）
_KNOWLEDGE_FILE_MAX_SIZE = 10 * 1024 * 1024  # 10MB
_KNOWLEDGE_FILE_ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


@router.post("/knowledge/items/{item_id}/file", response_model=FileInfoResponse)
async def replace_knowledge_item_file(
    item_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    user: JWTClaims = Depends(require_role("admin")),
) -> FileInfoResponse:
    """ソースファイルを差し替える（admin only）。LLM再抽出は行わない。"""
    import os

    db = get_service_client()

    # knowledge_itemの存在確認（company_idフィルタ）
    item_result = db.table("knowledge_items") \
        .select("session_id") \
        .eq("id", str(item_id)) \
        .eq("company_id", str(user.company_id)) \
        .single() \
        .execute()

    if not item_result.data:
        raise HTTPException(status_code=404, detail="Knowledge item not found")

    session_id = item_result.data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=422, detail="Knowledge item has no associated session")

    # ファイル拡張子チェック
    filename = file.filename or "unknown"
    _, ext = os.path.splitext(filename)
    ext = ext.lower()
    if ext not in _KNOWLEDGE_FILE_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(_KNOWLEDGE_FILE_ALLOWED_EXTENSIONS.keys()))}",
        )

    content = await file.read()
    if len(content) > _KNOWLEDGE_FILE_MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large: {len(content)} bytes. Maximum: {_KNOWLEDGE_FILE_MAX_SIZE} bytes (10MB).",
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    content_type = _KNOWLEDGE_FILE_ALLOWED_EXTENSIONS[ext]

    # セッション情報取得（既存ファイルパスを確認）
    session_result = db.table("knowledge_sessions") \
        .select("id, file_storage_path") \
        .eq("id", str(session_id)) \
        .eq("company_id", str(user.company_id)) \
        .single() \
        .execute()

    if not session_result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    old_path: Optional[str] = session_result.data.get("file_storage_path")

    # 古いファイルをStorageから削除
    if old_path:
        try:
            db.storage.from_("knowledge-files").remove([old_path])
        except Exception as e:
            logger.warning(f"Failed to delete old file {old_path}: {e}")

    # 新しいファイルをStorageにアップロード
    new_path = f"{user.company_id}/{session_id}/{filename}"
    try:
        db.storage.from_("knowledge-files").upload(
            new_path,
            content,
            {"content-type": content_type},
        )
    except Exception as e:
        logger.error(f"Failed to upload new file to Storage {new_path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload file to storage")

    # knowledge_sessionsのファイルメタデータ更新
    try:
        db.table("knowledge_sessions").update({
            "file_name": filename,
            "file_size": len(content),
            "file_content_type": content_type,
            "file_storage_path": new_path,
        }).eq("id", str(session_id)).execute()
    except Exception as e:
        logger.error(f"Failed to update file metadata for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update file metadata")

    # 署名付きURL発行（有効期限1時間）
    try:
        signed = db.storage.from_("knowledge-files").create_signed_url(new_path, 3600)
        download_url: str = signed.get("signedURL") or signed.get("signed_url") or ""
        if not download_url:
            raise ValueError("No signed URL returned")
    except Exception as e:
        logger.error(f"Failed to create signed URL for {new_path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="update",
        resource_type="knowledge_item_file",
        resource_id=str(item_id),
        details={
            "session_id": str(session_id),
            "old_storage_path": old_path,
            "new_storage_path": new_path,
            "file_name": filename,
            "file_size": len(content),
        },
        ip_address=request.client.host if request.client else None,
    )

    return FileInfoResponse(
        file_name=filename,
        file_size=len(content),
        file_content_type=content_type,
        download_url=download_url,
        created_at=datetime.utcnow(),
    )


# ─────────────────────────────────────
# Q&A フィードバック
# ─────────────────────────────────────

class QARatingRequest(BaseModel):
    user_rating: int  # 1=良い, -1=悪い
    rating_comment: Optional[str] = None


class QARatingResponse(BaseModel):
    session_id: str
    user_rating: int
    message: str


@router.patch("/knowledge/qa/{session_id}/rate", response_model=QARatingResponse)
async def rate_qa_session(
    session_id: str,
    body: QARatingRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """Q&A回答の評価を記録（👍👎）"""
    if body.user_rating not in (1, -1):
        raise HTTPException(
            status_code=422,
            detail="user_rating must be 1 (good) or -1 (bad)",
        )

    db = get_service_client()

    current = db.table("qa_sessions").select("id").eq(
        "id", session_id
    ).eq("company_id", str(user.company_id)).single().execute()

    if not current.data:
        raise HTTPException(status_code=404, detail="QA session not found")

    # DB の user_rating は TEXT型（008定義）→ 変換して保存
    rating_text = "helpful" if body.user_rating == 1 else "wrong"

    update_payload: dict = {"user_rating": rating_text}
    if body.rating_comment is not None:
        update_payload["user_feedback"] = body.rating_comment

    try:
        db.table("qa_sessions").update(update_payload).eq(
            "id", session_id
        ).eq("company_id", str(user.company_id)).execute()
    except Exception as e:
        logger.error(f"rate_qa_session failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return QARatingResponse(
        session_id=session_id,
        user_rating=body.user_rating,
        message="評価を記録しました",
    )


class TrendStats(BaseModel):
    total: int
    helpful: int
    wrong: int
    positive_rate: float


class QAStatsResponse(BaseModel):
    total_sessions: int
    rated_sessions: int
    helpful_count: int
    wrong_count: int
    positive_rate: float
    unrated_count: int
    avg_confidence: float
    recent_trend: dict


@router.get("/knowledge/qa/stats", response_model=QAStatsResponse)
async def get_qa_stats(user: JWTClaims = Depends(get_current_user)):
    """Q&A品質統計"""
    from datetime import timedelta

    db = get_service_client()

    all_sessions = db.table("qa_sessions").select(
        "id, user_rating, confidence, created_at"
    ).eq("company_id", str(user.company_id)).execute()

    rows = all_sessions.data or []
    total = len(rows)
    helpful = sum(1 for r in rows if r.get("user_rating") == "helpful")
    wrong = sum(1 for r in rows if r.get("user_rating") == "wrong")
    rated = helpful + wrong
    unrated = total - rated
    positive_rate = helpful / rated if rated > 0 else 0.0

    confidences = [float(r["confidence"]) for r in rows if r.get("confidence") is not None]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    now = datetime.utcnow()

    def trend_for_days(days: int) -> dict:
        cutoff = (now - timedelta(days=days)).isoformat()
        recent = [r for r in rows if (r.get("created_at") or "") >= cutoff]
        h = sum(1 for r in recent if r.get("user_rating") == "helpful")
        w = sum(1 for r in recent if r.get("user_rating") == "wrong")
        r_total = h + w
        return {
            "total": len(recent),
            "helpful": h,
            "wrong": w,
            "positive_rate": round(h / r_total, 4) if r_total > 0 else 0.0,
        }

    return QAStatsResponse(
        total_sessions=total,
        rated_sessions=rated,
        helpful_count=helpful,
        wrong_count=wrong,
        positive_rate=round(positive_rate, 4),
        unrated_count=unrated,
        avg_confidence=round(avg_conf, 4),
        recent_trend={
            "last_7_days": trend_for_days(7),
            "last_30_days": trend_for_days(30),
        },
    )

"""バックグラウンドジョブ状態参照（b_10 §3-4）。"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


class JobStatusResponse(BaseModel):
    job_id: str
    company_id: str
    job_type: str
    status: str
    payload: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


def _row_to_response(row: dict) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=str(row["id"]),
        company_id=str(row["company_id"]),
        job_type=row["job_type"],
        status=row["status"],
        payload=row.get("payload"),
        result=row.get("result"),
        error_message=row.get("error_message"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        created_at=row.get("created_at"),
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: UUID,
    user: JWTClaims = Depends(get_current_user),
) -> JobStatusResponse:
    """同一テナントのジョブ詳細を返す。"""
    try:
        db = get_service_client()
        res = (
            db.table("background_jobs")
            .select("*")
            .eq("id", str(job_id))
            .eq("company_id", str(user.company_id))
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="ジョブが見つかりません")
        return _row_to_response(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_job_status failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


class JobListResponse(BaseModel):
    items: list[JobStatusResponse]
    total: int


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    job_type: Optional[str] = Query(default=None, description="例: kintone_mfg_import"),
    limit: int = Query(default=20, ge=1, le=100),
    user: JWTClaims = Depends(require_role("admin")),
) -> JobListResponse:
    """直近のジョブ一覧（admin）。"""
    try:
        db = get_service_client()
        q = (
            db.table("background_jobs")
            .select("*", count="exact")
            .eq("company_id", str(user.company_id))
        )
        if job_type:
            q = q.eq("job_type", job_type)
        res = q.order("created_at", desc=True).limit(limit).execute()
        rows = res.data or []
        return JobListResponse(
            items=[_row_to_response(r) for r in rows],
            total=res.count if res.count is not None else len(rows),
        )
    except Exception as e:
        logger.error("list_jobs failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

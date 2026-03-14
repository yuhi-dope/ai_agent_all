"""BPO execution endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims

router = APIRouter()


class ExecutionRunRequest(BaseModel):
    flow_id: UUID
    parameters: Optional[dict] = None
    dry_run: bool = False


class ExecutionRunResponse(BaseModel):
    execution_id: UUID
    status: str  # "queued" | "running"
    estimated_duration_sec: Optional[int] = None


class ExecutionLogResponse(BaseModel):
    id: UUID
    flow_id: Optional[UUID]
    triggered_by: Optional[str]
    operations: dict
    overall_success: Optional[bool]
    time_saved_minutes: Optional[int]
    cost_saved_yen: Optional[int]
    created_at: datetime


class ExecutionLogListResponse(BaseModel):
    items: list[ExecutionLogResponse]
    total: int
    has_more: bool = False


@router.post("/execution/run", response_model=ExecutionRunResponse)
async def execute_bpo(
    body: ExecutionRunRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """BPOタスク実行（admin のみ）"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/execution/logs", response_model=ExecutionLogListResponse)
async def list_execution_logs(
    flow_id: Optional[UUID] = None,
    overall_success: Optional[bool] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """実行ログ一覧"""
    raise HTTPException(status_code=501, detail="Not implemented")

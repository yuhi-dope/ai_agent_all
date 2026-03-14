"""Digital Twin snapshot endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_DIMENSIONS = {"people", "process", "cost", "tool", "risk"}


class SnapshotResponse(BaseModel):
    id: UUID
    snapshot_at: datetime
    people_state: Optional[dict] = None
    process_state: Optional[dict] = None
    cost_state: Optional[dict] = None
    tool_state: Optional[dict] = None
    risk_state: Optional[dict] = None


class SnapshotUpdateRequest(BaseModel):
    dimension: str  # people / process / cost / tool / risk
    data: dict


@router.get("/twin/snapshot", response_model=SnapshotResponse)
async def get_latest_snapshot(
    user: JWTClaims = Depends(get_current_user),
):
    """最新のデジタルツインスナップショット取得"""
    db = get_service_client()
    result = db.table("company_state_snapshots") \
        .select("*") \
        .eq("company_id", user.company_id) \
        .order("snapshot_at", desc=True) \
        .limit(1) \
        .execute()

    if not result.data:
        # Create initial empty snapshot
        new = db.table("company_state_snapshots").insert({
            "company_id": user.company_id,
        }).execute()
        return SnapshotResponse(**new.data[0])

    return SnapshotResponse(**result.data[0])


@router.post("/twin/snapshot", response_model=SnapshotResponse)
async def update_snapshot(
    body: SnapshotUpdateRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """特定次元の手動更新（admin のみ）— 新スナップショットを作成"""
    if body.dimension not in VALID_DIMENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid dimension: {body.dimension}. Must be one of: {VALID_DIMENSIONS}",
        )

    db = get_service_client()

    # Fetch current latest snapshot
    current = db.table("company_state_snapshots") \
        .select("*") \
        .eq("company_id", user.company_id) \
        .order("snapshot_at", desc=True) \
        .limit(1) \
        .execute()

    # Build new snapshot (copy existing + update one dimension)
    new_data = {"company_id": user.company_id}
    if current.data:
        for dim in VALID_DIMENSIONS:
            col = f"{dim}_state"
            new_data[col] = current.data[0].get(col)

    # Update the specified dimension
    new_data[f"{body.dimension}_state"] = body.data

    result = db.table("company_state_snapshots").insert(new_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create snapshot")

    return SnapshotResponse(**result.data[0])

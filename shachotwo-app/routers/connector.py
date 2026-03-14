"""SaaS connector management endpoints."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.middleware import require_role
from auth.jwt import JWTClaims

router = APIRouter()


class ConnectorCreate(BaseModel):
    tool_name: str
    tool_type: str  # "saas" | "cli" | "api" | "manual"
    connection_config: dict


class ConnectorResponse(BaseModel):
    id: UUID
    tool_name: str
    tool_type: str
    connection_method: str
    health_status: str
    last_health_check: Optional[datetime] = None


class ConnectorListResponse(BaseModel):
    items: list[ConnectorResponse]
    total: int


@router.get("/connectors", response_model=ConnectorListResponse)
async def list_connectors(
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ一覧（admin のみ）"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/connectors", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    body: ConnectorCreate,
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ接続（admin のみ）"""
    raise HTTPException(status_code=501, detail="Not implemented")


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: UUID,
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ削除（admin のみ）"""
    raise HTTPException(status_code=501, detail="Not implemented")

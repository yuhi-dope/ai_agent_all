"""Company management endpoints."""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


# --- Models ---

class CompanyResponse(BaseModel):
    """企業情報レスポンス"""
    id: UUID
    name: str
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    plan: Optional[str] = None
    genome_customizations: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class CompanyUpdate(BaseModel):
    """企業情報更新リクエスト（admin のみ）"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    industry: Optional[str] = Field(None, max_length=100)
    employee_count: Optional[int] = Field(None, ge=1)
    plan: Optional[str] = Field(None, max_length=50)
    genome_customizations: Optional[dict] = None
    allowed_domains: Optional[list[str]] = Field(
        None,
        description="招待メールの許可ドメイン一覧（例: ['minato.co.jp']）。空リストで制限なし。",
    )


# --- Endpoints ---

COMPANY_SELECT_COLUMNS = (
    "id, name, industry, employee_count, plan, "
    "genome_customizations, created_at, updated_at"
)


@router.get("/companies/me", response_model=CompanyResponse)
async def get_my_company(
    user: JWTClaims = Depends(get_current_user),
):
    """自社の企業情報を取得"""
    db = get_service_client()
    result = db.table("companies") \
        .select(COMPANY_SELECT_COLUMNS) \
        .eq("id", user.company_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    return CompanyResponse(**result.data)


@router.patch("/companies/me", response_model=CompanyResponse)
async def update_my_company(
    body: CompanyUpdate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """自社の企業情報を更新（admin のみ）"""
    # Build update payload from non-None fields
    update_data: dict = {}
    for field_name, value in body.model_dump(exclude_unset=True).items():
        update_data[field_name] = value

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    db = get_service_client()

    # Verify company exists
    current = db.table("companies") \
        .select("id") \
        .eq("id", user.company_id) \
        .single() \
        .execute()

    if not current.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Perform update
    result = db.table("companies") \
        .update(update_data) \
        .eq("id", user.company_id) \
        .execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Update failed",
        )

    updated = result.data[0]

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="update",
        resource_type="company",
        resource_id=user.company_id,
        details={"updated_fields": update_data},
        ip_address=request.client.host if request.client else None,
    )

    return CompanyResponse(**updated)


# --- NPS収集 ---

class NPSRequest(BaseModel):
    score: int = Field(..., ge=0, le=10, description="NPS スコア (0-10)")
    comment: Optional[str] = Field(None, max_length=1000)

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if not (0 <= v <= 10):
            raise ValueError("score must be between 0 and 10")
        return v


@router.post("/company/nps", status_code=status.HTTP_204_NO_CONTENT)
async def submit_nps(
    body: NPSRequest,
    request: Request,
    user: JWTClaims = Depends(get_current_user),
):
    """NPS（サービス満足度）スコアを記録する。

    audit_logs テーブルを利用して保存し、専用テーブルは不要（Phase 2+ で集計基盤追加）。
    """
    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="nps_submit",
        resource_type="nps",
        resource_id=user.company_id,
        details={
            "score": body.score,
            "comment": body.comment,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        },
        ip_address=request.client.host if request.client else None,
    )

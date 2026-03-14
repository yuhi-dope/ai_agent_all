"""User management endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    """ユーザー作成リクエスト"""
    email: str
    full_name: str
    role: str  # "admin" | "editor"
    department: Optional[str] = None


class UserResponse(BaseModel):
    """ユーザーレスポンス"""
    id: UUID
    company_id: UUID
    email: str
    full_name: str
    role: str
    department: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserUpdate(BaseModel):
    """ユーザー更新リクエスト"""
    role: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None


class UserListResponse(BaseModel):
    """ユーザー一覧レスポンス"""
    items: list[UserResponse]
    total: int
    has_more: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ROLES = {"admin", "editor"}

SELECT_COLUMNS = "id, company_id, email, full_name, role, department, is_active, created_at, updated_at"


def _validate_company_access(user: JWTClaims, company_id: UUID) -> None:
    """JWTの company_id とパスパラメータの company_id が一致するか検証"""
    if user.company_id != str(company_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="他社のリソースにはアクセスできません",
        )


def _validate_role(role: str) -> None:
    """ロール値が有効か検証"""
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role は {'/'.join(VALID_ROLES)} のいずれかを指定してください",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/companies/{company_id}/users", response_model=UserListResponse)
async def list_users(
    company_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(require_role("admin")),
):
    """ユーザー一覧（admin のみ）"""
    _validate_company_access(user, company_id)

    db = get_service_client()
    result = (
        db.table("users")
        .select(SELECT_COLUMNS, count="exact")
        .eq("company_id", str(company_id))
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )

    total = result.count or 0
    return UserListResponse(
        items=[UserResponse(**r) for r in result.data],
        total=total,
        has_more=(offset + limit) < total,
    )


@router.post(
    "/companies/{company_id}/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    company_id: UUID,
    body: UserCreate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """ユーザー追加（admin のみ）"""
    _validate_company_access(user, company_id)
    _validate_role(body.role)

    db = get_service_client()

    # メールアドレスの重複チェック（同一企業内）
    existing = (
        db.table("users")
        .select("id")
        .eq("company_id", str(company_id))
        .eq("email", body.email)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このメールアドレスは既に登録されています",
        )

    new_user = {
        "id": str(uuid4()),
        "company_id": str(company_id),
        "email": body.email,
        "full_name": body.full_name,
        "role": body.role,
        "department": body.department,
        "is_active": True,
    }

    result = (
        db.table("users")
        .insert(new_user)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="ユーザー作成に失敗しました")

    created = result.data[0]

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="create",
        resource_type="user",
        resource_id=created["id"],
        details={"email": body.email, "role": body.role},
        ip_address=request.client.host if request.client else None,
    )

    return UserResponse(**created)


@router.patch("/companies/{company_id}/users/{user_id}", response_model=UserResponse)
async def update_user(
    company_id: UUID,
    user_id: UUID,
    body: UserUpdate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """ユーザー更新（admin のみ）"""
    _validate_company_access(user, company_id)

    if body.role is not None:
        _validate_role(body.role)

    db = get_service_client()

    # 対象ユーザーの存在・所属確認
    current = (
        db.table("users")
        .select("id")
        .eq("id", str(user_id))
        .eq("company_id", str(company_id))
        .execute()
    )

    if not current.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ユーザーが見つかりません",
        )

    # 更新データ構築（None のフィールドは除外）
    update_data: dict = {}
    if body.role is not None:
        update_data["role"] = body.role
    if body.department is not None:
        update_data["department"] = body.department
    if body.is_active is not None:
        update_data["is_active"] = body.is_active

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="更新するフィールドを1つ以上指定してください",
        )

    result = (
        db.table("users")
        .update(update_data)
        .eq("id", str(user_id))
        .eq("company_id", str(company_id))
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=500, detail="ユーザー更新に失敗しました")

    updated = result.data[0]

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="update",
        resource_type="user",
        resource_id=str(user_id),
        details={"new_values": update_data},
        ip_address=request.client.host if request.client else None,
    )

    return UserResponse(**updated)

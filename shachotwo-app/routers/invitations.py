"""Invitation endpoints — admin invites members to the same company."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr

from auth.middleware import require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class InvitationCreate(BaseModel):
    """招待作成リクエスト"""
    email: str
    role: str = "editor"  # "admin" | "editor"
    name: Optional[str] = None


class InvitationResponse(BaseModel):
    """招待レスポンス"""
    id: UUID
    company_id: UUID
    email: str
    role: str
    invited_by: UUID
    status: str
    expires_at: datetime
    created_at: datetime


class InvitationListResponse(BaseModel):
    """招待一覧レスポンス"""
    items: list[InvitationResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_ROLES = {"admin", "editor"}


def _validate_company_access(user: JWTClaims, company_id: UUID) -> None:
    if user.company_id != str(company_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="他社のリソースにはアクセスできません",
        )


def _validate_role(role: str) -> None:
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"role は {'/'.join(VALID_ROLES)} のいずれかを指定してください",
        )


def _validate_email_domain(email: str, allowed_domains: list[str]) -> None:
    """メールアドレスのドメインが許可リストに含まれるか検証する。

    allowed_domains が空の場合は制限なし（移行期・小規模テナント向け）。
    """
    if not allowed_domains:
        return  # 制限なし

    domain = email.split("@")[-1].lower()
    normalized = [d.lower().lstrip("@") for d in allowed_domains]
    if domain not in normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"招待できるのは会社ドメイン（{', '.join(normalized)}）のメールアドレスのみです。"
                " 社外のアドレスには招待を送れません。"
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/companies/{company_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    company_id: UUID,
    body: InvitationCreate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """メンバー招待（admin のみ）

    1. 重複チェック（既存ユーザー / pending招待）
    2. Supabase Auth で招待メール送信
    3. 招待レコード作成
    """
    _validate_company_access(user, company_id)
    _validate_role(body.role)

    db = get_service_client()

    # 会社のドメイン制限を取得してメールドメインを検証
    company_res = (
        db.table("companies")
        .select("allowed_domains")
        .eq("id", str(company_id))
        .maybe_single()
        .execute()
    )
    allowed_domains: list[str] = (company_res.data or {}).get("allowed_domains") or []
    _validate_email_domain(body.email, allowed_domains)

    # 既に同じ会社のユーザーとして登録済みか
    existing_user = (
        db.table("users")
        .select("id")
        .eq("company_id", str(company_id))
        .eq("email", body.email)
        .execute()
    )
    if existing_user.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このメールアドレスは既にメンバーとして登録されています",
        )

    # pending の招待が既にあるか
    existing_invitation = (
        db.table("invitations")
        .select("id")
        .eq("company_id", str(company_id))
        .eq("email", body.email)
        .eq("status", "pending")
        .execute()
    )
    if existing_invitation.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このメールアドレスには既に招待を送信済みです",
        )

    # Supabase Auth で招待（メール送信 + auth.users 作成）
    try:
        invite_res = db.auth.admin.invite_user_by_email(
            body.email,
            options={
                "data": {
                    "full_name": body.name or "",
                    "invited_company_id": str(company_id),
                    "invited_role": body.role,
                },
            },
        )
        auth_user_id = invite_res.user.id if invite_res.user else None
    except Exception as e:
        error_msg = str(e)
        # ユーザーが既にSupabase Authに存在する場合
        if "already been registered" in error_msg or "already exists" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="このメールアドレスは既にアカウントが存在します。ログインしてもらってください。",
            )
        logger.error(f"Supabase invite failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="招待メールの送信に失敗しました",
        )

    # 招待されたユーザーの app_metadata に company_id と role を設定
    if auth_user_id:
        try:
            db.auth.admin.update_user_by_id(
                str(auth_user_id),
                {"app_metadata": {"company_id": str(company_id), "role": body.role}},
            )
        except Exception as e:
            logger.error(f"Failed to set app_metadata for invited user: {e}")

    # invitations テーブルにレコード作成
    invitation_data = {
        "company_id": str(company_id),
        "email": body.email,
        "role": body.role,
        "invited_by": user.sub,
        "status": "pending",
    }

    result = db.table("invitations").insert(invitation_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="招待の作成に失敗しました")

    created = result.data[0]

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="create",
        resource_type="invitation",
        resource_id=created["id"],
        details={"email": body.email, "role": body.role},
        ip_address=request.client.host if request.client else None,
    )

    return InvitationResponse(**created)


@router.get(
    "/companies/{company_id}/invitations",
    response_model=InvitationListResponse,
)
async def list_invitations(
    company_id: UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    user: JWTClaims = Depends(require_role("admin")),
):
    """招待一覧（admin のみ）"""
    _validate_company_access(user, company_id)

    db = get_service_client()
    query = (
        db.table("invitations")
        .select("*", count="exact")
        .eq("company_id", str(company_id))
        .order("created_at", desc=True)
    )

    if status_filter:
        query = query.eq("status", status_filter)

    result = query.execute()

    return InvitationListResponse(
        items=[InvitationResponse(**r) for r in result.data],
        total=result.count or 0,
    )


@router.delete(
    "/companies/{company_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_invitation(
    company_id: UUID,
    invitation_id: UUID,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """招待キャンセル（admin のみ）"""
    _validate_company_access(user, company_id)

    db = get_service_client()

    # 対象の招待を確認
    existing = (
        db.table("invitations")
        .select("id, status")
        .eq("id", str(invitation_id))
        .eq("company_id", str(company_id))
        .execute()
    )

    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="招待が見つかりません",
        )

    if existing.data[0]["status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="この招待はキャンセルできません",
        )

    db.table("invitations").update({"status": "cancelled"}).eq(
        "id", str(invitation_id)
    ).execute()

    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="cancel",
        resource_type="invitation",
        resource_id=str(invitation_id),
        details={},
        ip_address=request.client.host if request.client else None,
    )

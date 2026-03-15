"""Post-registration setup: create company + set app_metadata."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi import Depends
from pydantic import BaseModel

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer()


class SetupRequest(BaseModel):
    company_name: str
    industry: str = "その他"
    corporate_number: Optional[str] = None
    company_location: Optional[str] = None


class SetupResponse(BaseModel):
    company_id: str
    role: str
    message: str


@router.post("/auth/setup", response_model=SetupResponse)
async def setup_account(
    body: SetupRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """新規登録後のセットアップ。

    1. Supabase Admin APIでトークン検証（app_metadata未設定でもOK）
    2. companies テーブルにレコード作成
    3. users テーブルにレコード作成
    4. app_metadata に company_id + role を設定
    """
    db = get_service_client()

    # 1. Verify token via Supabase Admin API
    try:
        res = db.auth.get_user(credentials.credentials)
        user = res.user
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    user_id = user.id
    email = user.email or ""
    user_metadata = user.user_metadata or {}
    app_metadata = user.app_metadata or {}

    # Already set up?
    if app_metadata.get("company_id"):
        # 招待ユーザー: app_metadata は設定済みだが users レコードがまだない場合
        company_id = app_metadata["company_id"]
        role = app_metadata.get("role", "editor")

        existing_user = (
            db.table("users")
            .select("id")
            .eq("id", user_id)
            .execute()
        )

        if not existing_user.data:
            # users レコードを作成（招待経由の初回ログイン）
            user_data = {
                "id": user_id,
                "company_id": company_id,
                "email": email,
                "name": user_metadata.get("full_name") or email.split("@")[0],
                "role": role,
                "department": None,
            }
            try:
                db.table("users").insert(user_data).execute()
            except Exception as e:
                logger.error(f"Invited user creation failed: {e}")

            # 招待レコードを accepted に更新
            try:
                db.table("invitations").update(
                    {"status": "accepted", "accepted_at": "now()"}
                ).eq("email", email).eq("company_id", company_id).eq(
                    "status", "pending"
                ).execute()
            except Exception as e:
                logger.error(f"Invitation status update failed: {e}")

        return SetupResponse(
            company_id=company_id,
            role=role,
            message="セットアップ完了",
        )

    # 2. Create company (通常の新規登録)
    company_data = {
        "name": body.company_name,
        "industry": body.industry,
    }
    result = db.table("companies").insert(company_data).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="企業の作成に失敗しました")

    company_id = result.data[0]["id"]

    # 3. Create user record
    user_data = {
        "id": user_id,
        "company_id": company_id,
        "email": email,
        "name": user_metadata.get("full_name", email.split("@")[0]),
        "role": "admin",
        "department": None,
    }

    try:
        db.table("users").insert(user_data).execute()
    except Exception as e:
        logger.error(f"User creation failed: {e}")
        db.table("companies").delete().eq("id", company_id).execute()
        raise HTTPException(status_code=500, detail="ユーザーの作成に失敗しました")

    # 4. Set app_metadata
    try:
        db.auth.admin.update_user_by_id(
            user_id,
            {"app_metadata": {"company_id": company_id, "role": "admin"}},
        )
    except Exception as e:
        logger.error(f"Failed to set app_metadata: {e}")

    return SetupResponse(
        company_id=company_id,
        role="admin",
        message="セットアップ完了",
    )

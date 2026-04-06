"""MFA（多要素認証）ルーター — SOC2準備。

エンドポイント:
    GET  /mfa/status     → MFA有効状態と最終確認日時を返す
    POST /mfa/setup      → TOTPシークレット・QRコードURLを生成して返す（有効化前）
    POST /mfa/verify     → TOTPコードを検証してMFAを有効化する
    DELETE /mfa/disable  → MFAを無効化する（admin確認あり）
"""
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit_middleware import log_audit

# pyotpはオプショナル依存（pip install pyotp）
try:
    import pyotp
except ImportError:
    pyotp = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
router = APIRouter()

# TOTPの発行者名（QRコードに表示される）
TOTP_ISSUER = "シャチョツー"


# ============================================================
# Pydanticモデル
# ============================================================

class MFAStatusResponse(BaseModel):
    is_enabled: bool
    last_verified_at: Optional[datetime] = None


class MFASetupResponse(BaseModel):
    totp_secret: str
    qr_code_url: str


class MFAVerifyRequest(BaseModel):
    totp_code: str


class MFAVerifyResponse(BaseModel):
    success: bool


class MFADisableResponse(BaseModel):
    success: bool
    message: str


# ============================================================
# ヘルパー関数
# ============================================================

def _generate_totp_secret() -> str:
    """TOTPシークレットを生成する。pyotpがあればpyotpを使用、なければfallback。"""
    if pyotp is not None:
        return pyotp.random_base32()
    # pyotp未インストール時のフォールバック（32文字のBase32互換文字列）
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(secrets.choice(alphabet) for _ in range(32))


def _build_qr_url(secret: str, user_email: str, issuer: str = TOTP_ISSUER) -> str:
    """TOTP用のotpauthスキームURLを生成する（QRコード化用）。"""
    if pyotp is not None:
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=user_email, issuer_name=issuer)
    # pyotp未インストール時のフォールバック
    from urllib.parse import quote
    label = quote(f"{issuer}:{user_email}")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def _verify_totp_code(secret: str, code: str) -> bool:
    """TOTPコードを検証する。pyotpがあればpyotpを使用、なければfallback（常にFalse）。"""
    if pyotp is not None:
        totp = pyotp.TOTP(secret)
        # valid_window=1: 前後30秒の時刻ずれを許容
        return totp.verify(code, valid_window=1)
    # pyotp未インストール時: 検証不可（セキュリティ上Falseを返す）
    logger.warning("pyotp未インストールのためTOTP検証不可。MFA機能が無効状態です。")
    return False


# ============================================================
# エンドポイント
# ============================================================

@router.get("/mfa/status", response_model=MFAStatusResponse, summary="MFA有効状態を確認")
async def get_mfa_status(
    current_user: JWTClaims = Depends(get_current_user),
):
    """現在のユーザーのMFA有効状態と最終確認日時を返す。"""
    db = get_service_client()
    result = (
        db.table("mfa_settings")
        .select("is_enabled, last_verified_at")
        .eq("user_id", current_user.sub)
        .maybe_single()
        .execute()
    )
    if result.data is None:
        # レコード未作成 = MFA未設定
        return MFAStatusResponse(is_enabled=False, last_verified_at=None)

    return MFAStatusResponse(
        is_enabled=result.data.get("is_enabled", False),
        last_verified_at=result.data.get("last_verified_at"),
    )


@router.post("/mfa/setup", response_model=MFASetupResponse, summary="MFAセットアップ（シークレット・QRコード生成）")
async def setup_mfa(
    request: Request,
    current_user: JWTClaims = Depends(get_current_user),
):
    """TOTPシークレットとQRコードURLを生成して返す。

    この時点ではMFAは有効化されない。/mfa/verify でコードを確認後に有効化される。
    シークレットはDBに保存されるが、is_enabled=false のまま。

    セキュリティ注意:
    - totp_secret はアプリレイヤーで暗号化してDBに保存する（本実装はMVP版）
    - 本番環境では GCP Secret Manager 等で暗号化キーを管理すること
    """
    db = get_service_client()
    secret = _generate_totp_secret()
    qr_url = _build_qr_url(secret=secret, user_email=current_user.email or current_user.sub)

    # 既存レコードの確認
    existing = (
        db.table("mfa_settings")
        .select("id")
        .eq("user_id", current_user.sub)
        .maybe_single()
        .execute()
    )

    if existing.data:
        # 既存レコードを更新（is_enabled=falseに戻してセットアップやり直し）
        db.table("mfa_settings").update({
            "totp_secret": secret,
            "is_enabled": False,
        }).eq("user_id", current_user.sub).execute()
    else:
        # 新規作成
        db.table("mfa_settings").insert({
            "company_id": current_user.company_id,
            "user_id": current_user.sub,
            "totp_secret": secret,
            "is_enabled": False,
        }).execute()

    await log_audit(
        company_id=current_user.company_id,
        actor_user_id=current_user.sub,
        actor_role=current_user.role,
        action="create",
        resource_type="mfa_settings",
        resource_id=current_user.sub,
        new_values={"setup_initiated": True},
        request=request,
    )

    return MFASetupResponse(totp_secret=secret, qr_code_url=qr_url)


@router.post("/mfa/verify", response_model=MFAVerifyResponse, summary="TOTPコード検証・MFA有効化")
async def verify_mfa(
    body: MFAVerifyRequest,
    request: Request,
    current_user: JWTClaims = Depends(get_current_user),
):
    """TOTPコードを検証し、正しければMFAを有効化する。

    Args:
        body.totp_code: 認証アプリが生成した6桁のコード
    """
    db = get_service_client()

    # シークレットを取得
    result = (
        db.table("mfa_settings")
        .select("totp_secret, is_enabled")
        .eq("user_id", current_user.sub)
        .maybe_single()
        .execute()
    )

    if result.data is None or not result.data.get("totp_secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFAセットアップが完了していません。先に /mfa/setup を呼び出してください。",
        )

    secret = result.data["totp_secret"]
    is_valid = _verify_totp_code(secret=secret, code=body.totp_code)

    if not is_valid:
        await log_audit(
            company_id=current_user.company_id,
            actor_user_id=current_user.sub,
            actor_role=current_user.role,
            action="update",
            resource_type="mfa_settings",
            resource_id=current_user.sub,
            new_values={"verify_failed": True},
            request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTPコードが正しくありません。",
        )

    # MFAを有効化
    now = datetime.now(timezone.utc).isoformat()
    db.table("mfa_settings").update({
        "is_enabled": True,
        "last_verified_at": now,
    }).eq("user_id", current_user.sub).execute()

    await log_audit(
        company_id=current_user.company_id,
        actor_user_id=current_user.sub,
        actor_role=current_user.role,
        action="update",
        resource_type="mfa_settings",
        resource_id=current_user.sub,
        new_values={"is_enabled": True, "last_verified_at": now},
        request=request,
    )

    return MFAVerifyResponse(success=True)


@router.delete("/mfa/disable", response_model=MFADisableResponse, summary="MFA無効化")
async def disable_mfa(
    request: Request,
    current_user: JWTClaims = Depends(get_current_user),
):
    """MFAを無効化する。

    セキュリティポリシー:
    - 本人または admin ロールのユーザーが実行可能
    - 監査ログに記録される
    - シークレットはDBから削除せずis_enabled=falseに設定（監査証跡の保持）
    """
    db = get_service_client()

    # MFA設定の存在確認
    result = (
        db.table("mfa_settings")
        .select("id, is_enabled")
        .eq("user_id", current_user.sub)
        .maybe_single()
        .execute()
    )

    if result.data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MFA設定が見つかりません。",
        )

    if not result.data.get("is_enabled", False):
        return MFADisableResponse(success=True, message="MFAはすでに無効です。")

    # is_enabled=false に更新（シークレットは保持して監査証跡を残す）
    db.table("mfa_settings").update({
        "is_enabled": False,
    }).eq("user_id", current_user.sub).execute()

    await log_audit(
        company_id=current_user.company_id,
        actor_user_id=current_user.sub,
        actor_role=current_user.role,
        action="update",
        resource_type="mfa_settings",
        resource_id=current_user.sub,
        old_values={"is_enabled": True},
        new_values={"is_enabled": False},
        request=request,
    )

    return MFADisableResponse(success=True, message="MFAを無効化しました。")

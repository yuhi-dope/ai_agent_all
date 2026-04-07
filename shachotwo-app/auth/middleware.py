"""FastAPI auth dependencies for route protection."""
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt import verify_jwt, JWTClaims, ROLE_LEVEL
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def _check_admin_mfa(user_id: str) -> None:
    """admin ロールの MFA 有効チェック。

    判定ルール:
    - mfa_settings レコードなし（未設定）→ 通過（猶予期間。設定画面で有効化を促す）
    - is_enabled = True                  → 通過
    - is_enabled = False（一度有効化後に無効化）→ 403

    エラー時（DB 接続失敗等）はフェイルオープン（ロックアウト防止）。
    """
    try:
        db = get_service_client()
        result = (
            db.table("mfa_settings")
            .select("is_enabled")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        # レコードなし＝未設定 → 猶予あり（ログインは通す）
        if result.data is None:
            logger.warning(f"admin user {user_id[:8]} has not set up MFA yet")
            return
        # 一度設定して明示的に無効化した場合のみブロック
        if result.data.get("is_enabled") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "MFA_REQUIRED",
                        "message": "管理者はMFAを有効にしてください",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        # DB 障害時はフェイルオープン（ロックアウト防止）してログだけ残す
        logger.error(f"MFA check failed for {user_id[:8]}: {e}")
    except Exception as e:
        logger.error(f"admin MFA チェック失敗（フェイルクローズ）: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "MFA_CHECK_FAILED",
                    "message": "MFA状態の確認に失敗しました。管理者にお問い合わせください。",
                }
            },
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> JWTClaims:
    """FastAPI dependency: extract and verify the current user from JWT.

    Usage:
        @router.get("/items")
        async def list_items(user: JWTClaims = Depends(get_current_user)):
            # user.company_id, user.role available
    """
    try:
        claims = await verify_jwt(credentials.credentials)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": str(e)}},
        )

    # Store in request state for downstream use
    request.state.company_id = claims.company_id
    request.state.user_id = claims.sub
    request.state.role = claims.role

    return claims


def require_role(*allowed_roles: str):
    """Dependency factory: require specific roles.

    admin ロールの場合は MFA 有効チェックを実施する（フェイルクローズ）。

    Usage:
        @router.post("/admin-only")
        async def action(user: JWTClaims = Depends(require_role("admin"))):
            ...
    """
    async def _check_role(
        user: JWTClaims = Depends(get_current_user),
    ) -> JWTClaims:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"この操作には {'/'.join(allowed_roles)} ロールが必要です",
                    }
                },
            )
        # admin ロールは MFA 必須
        if "admin" in allowed_roles or user.role == "admin":
            await _check_admin_mfa(user.sub)
        return user

    return _check_role


def require_min_role(min_role: str):
    """指定ロール以上の権限を要求するデコレータ。

    ROLE_LEVEL を使った階層チェック。admin ロールは MFA 有効チェックも実施する。

    Usage:
        @router.patch("/approve")
        async def approve(user: JWTClaims = Depends(require_min_role("approver"))):
            ...
    """
    min_level = ROLE_LEVEL.get(min_role, 0)

    async def _check_min_role(
        user: JWTClaims = Depends(get_current_user),
    ) -> JWTClaims:
        user_level = ROLE_LEVEL.get(user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"この操作には {min_role} 以上のロールが必要です",
                    }
                },
            )
        # admin ロールは MFA 必須
        if user.role == "admin":
            await _check_admin_mfa(user.sub)
        return user

    return _check_min_role

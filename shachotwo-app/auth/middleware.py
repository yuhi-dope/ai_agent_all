"""FastAPI auth dependencies for route protection."""
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt import verify_jwt, JWTClaims

logger = logging.getLogger(__name__)

security = HTTPBearer()


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
        return user

    return _check_role

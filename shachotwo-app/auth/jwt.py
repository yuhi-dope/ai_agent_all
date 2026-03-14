"""JWT verification for Supabase Auth tokens.

Uses Supabase Admin API (auth.get_user) for token verification
instead of manual JWT decode, for compatibility with all Supabase versions.
"""
import logging
from dataclasses import dataclass

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


@dataclass
class JWTClaims:
    """Parsed JWT claims from Supabase Auth."""
    sub: str            # Supabase Auth user ID
    company_id: str     # From app_metadata
    role: str           # 'admin' or 'editor'
    email: str
    exp: int = 0


async def verify_jwt(token: str) -> JWTClaims:
    """Verify a Supabase JWT via Admin API and extract claims.

    Raises:
        ValueError: If token is invalid, expired, or missing required claims.
    """
    try:
        db = get_service_client()
        res = db.auth.get_user(token)
        user = res.user
    except Exception as e:
        raise ValueError(f"Invalid JWT: {e}")

    if not user:
        raise ValueError("Invalid JWT: user not found")

    app_metadata = user.app_metadata or {}

    company_id = app_metadata.get("company_id")
    role = app_metadata.get("role")

    if not company_id or not role:
        raise ValueError(
            "SETUP_REQUIRED: app_metadata に company_id/role が未設定です。"
            "POST /api/v1/auth/setup を呼んでください。"
        )

    if role not in ("admin", "editor"):
        raise ValueError(f"Invalid role: {role}")

    return JWTClaims(
        sub=user.id,
        company_id=company_id,
        role=role,
        email=user.email or "",
    )

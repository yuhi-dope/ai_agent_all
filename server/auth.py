"""
Supabase Auth JWT 検証の FastAPI Dependency。
REQUIRE_AUTH=true で全 API に Bearer トークン必須化。false なら素通し。
"""

import logging
import os
from typing import Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def _require_auth() -> bool:
    val = os.environ.get("REQUIRE_AUTH", "true").strip().lower()
    return val not in ("false", "0", "no", "")


def _get_developer_emails() -> set[str]:
    raw = os.environ.get("DEVELOPER_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


_auth_client = None


def _get_auth_client():
    global _auth_client
    if _auth_client is not None:
        return _auth_client
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        _auth_client = create_client(url, key)
        return _auth_client
    except Exception:
        return None


async def get_current_user(request: Request) -> Optional[dict]:
    """
    FastAPI Dependency: Bearer トークンから Supabase ユーザーを取得。
    REQUIRE_AUTH=false なら X-Company-ID ヘッダーから company_id を読む匿名ユーザーを返す。
    戻り値: {"id": str, "email": str, "is_developer": bool, "company_id": str|None} or None
    """
    if not _require_auth():
        cid = request.headers.get("X-Company-ID", "").strip() or None
        return {
            "id": "anonymous",
            "email": "",
            "is_developer": True,
            "company_id": cid,
            "has_company": cid is not None,
            "company_role": "owner",  # no-auth モードでは全権限
        }

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Missing or invalid Authorization header"
        )

    token = auth_header[7:]
    client = _get_auth_client()
    if not client:
        raise HTTPException(
            status_code=500,
            detail="Auth not configured (SUPABASE_URL or SUPABASE_ANON_KEY missing)",
        )

    try:
        response = client.auth.get_user(token)
    except Exception as e:
        logger.warning("JWT verification failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if not response or not response.user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = response.user
    email = (user.email or "").lower()
    developer_emails = _get_developer_emails()

    # テナント情報を付与
    company_id = None
    company_role = None
    try:
        from server.company import get_user_companies

        companies = get_user_companies(str(user.id))
        if companies:
            company_id = companies[0].get("company_id")
            company_role = companies[0].get("role", "member")
    except Exception:
        pass

    # is_developer（DEVELOPER_EMAILS 登録済み）は owner 扱い
    if email in developer_emails:
        company_role = "owner"

    return {
        "id": user.id,
        "email": email,
        "is_developer": email in developer_emails,
        "company_id": company_id,
        "has_company": company_id is not None,
        "company_role": company_role or "member",
    }


async def get_admin_user(request: Request) -> Optional[dict]:
    """
    Admin 専用 Dependency: is_developer=True のユーザーのみ許可。
    REQUIRE_AUTH=false なら素通し。DEVELOPER_EMAILS 未設定なら全員許可。
    """
    user = await get_current_user(request)
    if user is None:
        return None  # 認証スキップ時
    developer_emails = _get_developer_emails()
    if not developer_emails:
        return user  # DEVELOPER_EMAILS 未設定 → 全員 admin 扱い
    if not user.get("is_developer"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

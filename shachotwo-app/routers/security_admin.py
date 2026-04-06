"""セキュリティ管理エンドポイント（SOC2対応）。

エンドポイント一覧:
  POST /admin/inactive-accounts/disable   非アクティブアカウント無効化実行（admin のみ）
  GET  /admin/inactive-accounts/preview   無効化対象アカウントのプレビュー（dry_run=True）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────

class InactiveAccountDisableResponse(BaseModel):
    disabled_count: int
    skipped_count: int
    dry_run: bool
    target_user_ids: list[str]


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.post(
    "/admin/inactive-accounts/disable",
    response_model=InactiveAccountDisableResponse,
    summary="非アクティブアカウントを無効化（SOC2 REQ-3002）",
)
async def disable_inactive_accounts_endpoint(
    user: JWTClaims = Depends(require_role("admin")),
):
    """90日間ログインがないアカウントを無効化する（dry_run=False で実行）。

    - admin ロールのみ実行可
    - profiles.is_active = false に更新
    - audit_logs に action="auto_disable" で記録
    """
    from workers.base.inactive_account_disabler import disable_inactive_accounts  # noqa: PLC0415

    try:
        result = await disable_inactive_accounts(dry_run=False)
    except Exception as exc:
        logger.error("disable_inactive_accounts_endpoint: unexpected error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"非アクティブアカウントの無効化中にエラーが発生しました: {exc}",
        ) from exc

    logger.info(
        "disable_inactive_accounts executed by admin user=%s disabled=%d",
        user.sub,
        result.get("disabled_count", 0),
    )
    return result


@router.get(
    "/admin/inactive-accounts/preview",
    response_model=InactiveAccountDisableResponse,
    summary="非アクティブアカウントの無効化対象プレビュー",
)
async def preview_inactive_accounts(
    user: JWTClaims = Depends(require_role("admin")),
):
    """90日間ログインがないアカウントを確認する（dry_run=True、DBを変更しない）。

    - admin ロールのみ実行可
    - 対象となるアカウントの一覧を返すだけで実際の無効化は行わない
    """
    from workers.base.inactive_account_disabler import disable_inactive_accounts  # noqa: PLC0415

    try:
        result = await disable_inactive_accounts(dry_run=True)
    except Exception as exc:
        logger.error("preview_inactive_accounts: unexpected error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"プレビュー取得中にエラーが発生しました: {exc}",
        ) from exc

    logger.info(
        "preview_inactive_accounts by admin user=%s targets=%d",
        user.sub,
        len(result.get("target_user_ids", [])),
    )
    return result

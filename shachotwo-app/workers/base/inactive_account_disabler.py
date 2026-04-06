"""非アクティブアカウント自動無効化。

SOC2要件（REQ-3002）: 90日間ログインがないアカウントを自動無効化する。
cron で毎日実行を想定。

実行方法:
    result = await disable_inactive_accounts(dry_run=False)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# 無効化の閾値（SOC2準拠: 90日）
_INACTIVE_THRESHOLD_DAYS = 90


async def disable_inactive_accounts(dry_run: bool = True) -> dict[str, Any]:
    """90日間ログインがないアカウントを無効化する。

    処理フロー:
    1. profiles テーブルの last_login_at が90日以上前のアカウントを取得
       （last_login_at が NULL の場合は created_at を使用）
    2. dry_run=True の場合は対象リストを返すだけ（DBを変更しない）
    3. dry_run=False の場合:
       - profiles.is_active = false に更新
       - audit_logs に action="auto_disable", resource_type="user" で記録
       - Slack / メール通知（notify_pipeline_event があれば使用）
    4. 結果サマリーを返す

    Args:
        dry_run: True の場合は対象確認のみ（デフォルト: True）

    Returns:
        {
            "disabled_count": int,
            "skipped_count": int,
            "dry_run": bool,
            "target_user_ids": list[str],
        }
    """
    db = get_service_client()
    threshold_dt = datetime.now(timezone.utc) - timedelta(days=_INACTIVE_THRESHOLD_DAYS)
    threshold_iso = threshold_dt.isoformat()

    logger.info(
        "disable_inactive_accounts: threshold=%s dry_run=%s",
        threshold_iso,
        dry_run,
    )

    # -----------------------------------------------------------
    # 1. 非アクティブ対象を取得
    #    last_login_at が NULL の場合は created_at で判定する。
    #    Supabase の RPC / postgrest では OR 条件で NULL も拾う。
    # -----------------------------------------------------------
    resp = (
        db.table("profiles")
        .select("id, last_login_at, created_at, is_active")
        .eq("is_active", True)
        .execute()
    )
    all_active: list[dict] = resp.data or []

    target_users: list[dict] = []
    skipped_users: list[dict] = []

    for profile in all_active:
        # last_login_at が NULL の場合は created_at を代替として使用
        login_str: str | None = profile.get("last_login_at") or profile.get("created_at")
        if not login_str:
            # どちらもなければ安全のためスキップ
            skipped_users.append(profile)
            continue

        # ISO 文字列をパース（末尾に +00:00 または Z が付いている想定）
        login_str_clean = login_str.replace("Z", "+00:00")
        try:
            last_dt = datetime.fromisoformat(login_str_clean)
        except ValueError:
            logger.warning(
                "disable_inactive_accounts: invalid datetime format user_id=%s value=%s",
                profile.get("id"),
                login_str,
            )
            skipped_users.append(profile)
            continue

        if last_dt < threshold_dt:
            target_users.append(profile)
        else:
            skipped_users.append(profile)

    target_user_ids = [u["id"] for u in target_users]

    logger.info(
        "disable_inactive_accounts: targets=%d skipped=%d",
        len(target_user_ids),
        len(skipped_users),
    )

    if dry_run:
        # dry_run は確認のみ — DB を変更しない
        return {
            "disabled_count": 0,
            "skipped_count": len(skipped_users),
            "dry_run": True,
            "target_user_ids": target_user_ids,
        }

    if not target_user_ids:
        return {
            "disabled_count": 0,
            "skipped_count": len(skipped_users),
            "dry_run": False,
            "target_user_ids": [],
        }

    # -----------------------------------------------------------
    # 3a. profiles.is_active = false に更新
    # -----------------------------------------------------------
    db.table("profiles").update({"is_active": False}).in_(
        "id", target_user_ids
    ).execute()
    logger.info(
        "disable_inactive_accounts: deactivated %d users",
        len(target_user_ids),
    )

    # -----------------------------------------------------------
    # 3b. audit_logs に記録（SOC2 証跡）
    # -----------------------------------------------------------
    now_iso = datetime.now(timezone.utc).isoformat()
    audit_records = [
        {
            "action": "auto_disable",
            "resource_type": "user",
            "resource_id": uid,
            "actor_id": "system",
            "details": {
                "reason": f"inactive_over_{_INACTIVE_THRESHOLD_DAYS}_days",
                "threshold_days": _INACTIVE_THRESHOLD_DAYS,
            },
            "created_at": now_iso,
        }
        for uid in target_user_ids
    ]
    try:
        db.table("audit_logs").insert(audit_records).execute()
    except Exception as exc:
        # 監査ログ失敗はエラーにしないが必ずログに残す
        logger.error(
            "disable_inactive_accounts: audit_log insert failed: %s", exc
        )

    # -----------------------------------------------------------
    # 3c. 通知（notify_pipeline_event が使える場合のみ）
    # -----------------------------------------------------------
    try:
        from workers.bpo.manager.notifier import notify_pipeline_event  # noqa: PLC0415

        await notify_pipeline_event(
            company_id="system",
            pipeline="security/inactive_account_disable",
            event_type="completed",
            details={
                "disabled_count": len(target_user_ids),
                "period": now_iso[:10],
                "threshold_days": _INACTIVE_THRESHOLD_DAYS,
            },
        )
    except Exception as exc:
        # 通知失敗は処理全体を止めない
        logger.warning(
            "disable_inactive_accounts: notification failed (non-fatal): %s", exc
        )

    return {
        "disabled_count": len(target_user_ids),
        "skipped_count": len(skipped_users),
        "dry_run": False,
        "target_user_ids": target_user_ids,
    }

"""非アクティブアカウント自動無効化のユニットテスト。

全て Supabase をモックし、DB 接続なしで実行する。
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _make_db_mock() -> MagicMock:
    """Supabase クライアントのチェーンモックを作る。"""
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.in_.return_value = db
    db.update.return_value = db
    db.insert.return_value = db
    db.limit.return_value = db
    db.order.return_value = db
    return db


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# 現在時刻を固定する基準（テスト内で「今」として使う）
_NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
_91_DAYS_AGO = _NOW - timedelta(days=91)
_89_DAYS_AGO = _NOW - timedelta(days=89)


# ─────────────────────────────────────
# test_dry_run_returns_targets_without_disabling
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_returns_targets_without_disabling():
    """dry_run=True の場合、対象リストを返すだけで DB を変更しない。"""
    db = _make_db_mock()

    # 91日前にログインしたユーザー（対象）
    inactive_user = {
        "id": "user-inactive",
        "last_login_at": _iso(_91_DAYS_AGO),
        "created_at": _iso(_91_DAYS_AGO),
        "is_active": True,
    }
    # 89日前にログインしたユーザー（対象外）
    active_user = {
        "id": "user-active",
        "last_login_at": _iso(_89_DAYS_AGO),
        "created_at": _iso(_89_DAYS_AGO),
        "is_active": True,
    }

    db.execute.return_value = MagicMock(data=[inactive_user, active_user])

    with patch(
        "workers.base.inactive_account_disabler.get_service_client",
        return_value=db,
    ):
        with patch(
            "workers.base.inactive_account_disabler.datetime",
        ) as mock_dt:
            # datetime.now() を固定
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from workers.base.inactive_account_disabler import disable_inactive_accounts
            result = await disable_inactive_accounts(dry_run=True)

    assert result["dry_run"] is True
    assert result["disabled_count"] == 0  # dry_run では無効化しない
    assert "user-inactive" in result["target_user_ids"]
    assert "user-active" not in result["target_user_ids"]

    # DB を変更していないことを確認（update が呼ばれていない）
    db.update.assert_not_called()


# ─────────────────────────────────────
# test_disable_inactive_updates_profiles
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_disable_inactive_updates_profiles():
    """dry_run=False の場合、is_active=False に更新し audit_logs に記録される。"""
    db = _make_db_mock()

    inactive_user = {
        "id": "user-inactive-001",
        "last_login_at": _iso(_91_DAYS_AGO),
        "created_at": _iso(_91_DAYS_AGO),
        "is_active": True,
    }

    db.execute.return_value = MagicMock(data=[inactive_user])

    with patch(
        "workers.base.inactive_account_disabler.get_service_client",
        return_value=db,
    ):
        with patch(
            "workers.base.inactive_account_disabler.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from workers.base.inactive_account_disabler import disable_inactive_accounts
            result = await disable_inactive_accounts(dry_run=False)

    assert result["dry_run"] is False
    assert result["disabled_count"] == 1
    assert "user-inactive-001" in result["target_user_ids"]

    # profiles の update が呼ばれたことを確認
    db.update.assert_called()
    # 最初の update 呼び出しが is_active=False を渡している
    update_calls = db.update.call_args_list
    assert any(
        call.args and call.args[0].get("is_active") is False
        for call in update_calls
    ), "profiles.is_active = False が呼ばれていない"

    # audit_logs の insert が呼ばれたことを確認
    db.insert.assert_called()
    insert_args = db.insert.call_args_list
    # insert に渡されたレコードに action="auto_disable" が含まれる
    assert any(
        isinstance(call.args[0], list)
        and any(r.get("action") == "auto_disable" for r in call.args[0])
        for call in insert_args
    ), "audit_logs に auto_disable が記録されていない"


# ─────────────────────────────────────
# test_accounts_active_within_90_days_not_targeted
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_accounts_active_within_90_days_not_targeted():
    """89日前にログインしたアカウントは対象にならない（境界値テスト）。"""
    db = _make_db_mock()

    # 89日前（閾値未満 → 対象外）
    recent_user = {
        "id": "user-recent",
        "last_login_at": _iso(_89_DAYS_AGO),
        "created_at": _iso(_89_DAYS_AGO),
        "is_active": True,
    }

    db.execute.return_value = MagicMock(data=[recent_user])

    with patch(
        "workers.base.inactive_account_disabler.get_service_client",
        return_value=db,
    ):
        with patch(
            "workers.base.inactive_account_disabler.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            from workers.base.inactive_account_disabler import disable_inactive_accounts
            result = await disable_inactive_accounts(dry_run=True)

    assert result["target_user_ids"] == []
    assert result["disabled_count"] == 0
    # skipped_count に含まれる
    assert result["skipped_count"] == 1

"""Tests for routers/users.py — PATCH user role, last-admin guard, Auth sync."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.users import router

COMPANY_ID = str(uuid.uuid4())
ADMIN_CALLER_ID = str(uuid.uuid4())
TARGET_USER_ID = str(uuid.uuid4())
OTHER_ADMIN_ID = str(uuid.uuid4())

ADMIN_USER = JWTClaims(
    sub=ADMIN_CALLER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)


def _make_client() -> TestClient:
    from auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    return TestClient(app, raise_server_exceptions=False)


def _updated_row(role: str = "editor") -> dict:
    return {
        "id": TARGET_USER_ID,
        "company_id": COMPANY_ID,
        "email": "target@example.com",
        "name": "Target",
        "role": role,
        "department": None,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _mock_db_for_role_patch(
    *,
    prev_role: str = "admin",
    other_active_admin: bool = True,
    auth_raises: bool = False,
    revert_ok: bool = True,
) -> MagicMock:
    """PATCH role 用: select(現ユーザー) → select(他admin) → update → 必要なら revert update。"""
    mock_db = MagicMock()
    chain = MagicMock()
    mock_db.table.return_value = chain

    exec_results: list[MagicMock] = [
        MagicMock(data=[{"id": TARGET_USER_ID, "role": prev_role}]),
    ]
    demote_guard_runs = prev_role == "admin"
    if demote_guard_runs:
        exec_results.append(
            MagicMock(
                data=[{"id": OTHER_ADMIN_ID}] if other_active_admin else []
            )
        )
    blocked_last_admin = demote_guard_runs and not other_active_admin
    if not blocked_last_admin:
        exec_results.append(MagicMock(data=[_updated_row("editor")]))

    if auth_raises:
        exec_results.append(
            MagicMock(data=[_updated_row("admin")] if revert_ok else [])
        )

    it = iter(exec_results)

    def _next_execute(*_a, **_k):
        return next(it)

    chain.execute.side_effect = _next_execute
    for name in ("select", "eq", "neq", "limit", "update"):
        getattr(chain, name).return_value = chain

    mock_auth = MagicMock()
    if auth_raises:
        mock_auth.admin.update_user_by_id.side_effect = RuntimeError("auth down")
    mock_db.auth = mock_auth

    return mock_db


class TestPatchUserRole:
    def test_patch_role_success_syncs_auth(self):
        client = _make_client()
        mock_db = _mock_db_for_role_patch(auth_raises=False)

        with patch("routers.users.get_service_client", return_value=mock_db), patch(
            "routers.users.audit_log", new_callable=AsyncMock
        ):
            res = client.patch(
                f"/companies/{COMPANY_ID}/users/{TARGET_USER_ID}",
                json={"role": "editor"},
            )

        assert res.status_code == 200
        assert res.json()["role"] == "editor"
        mock_db.auth.admin.update_user_by_id.assert_called_once()
        call_kw = mock_db.auth.admin.update_user_by_id.call_args
        assert call_kw[0][0] == TARGET_USER_ID
        assert call_kw[0][1]["app_metadata"]["role"] == "editor"
        assert call_kw[0][1]["app_metadata"]["company_id"] == COMPANY_ID

    def test_patch_last_admin_to_editor_conflict(self):
        client = _make_client()
        mock_db = _mock_db_for_role_patch(other_active_admin=False)

        with patch("routers.users.get_service_client", return_value=mock_db), patch(
            "routers.users.audit_log", new_callable=AsyncMock
        ):
            res = client.patch(
                f"/companies/{COMPANY_ID}/users/{TARGET_USER_ID}",
                json={"role": "editor"},
            )

        assert res.status_code == 409
        assert "最後の管理者" in res.json()["detail"]
        mock_db.auth.admin.update_user_by_id.assert_not_called()

    def test_patch_role_auth_failure_reverts_db(self):
        client = _make_client()
        mock_db = _mock_db_for_role_patch(auth_raises=True, revert_ok=True)

        with patch("routers.users.get_service_client", return_value=mock_db), patch(
            "routers.users.audit_log", new_callable=AsyncMock
        ):
            res = client.patch(
                f"/companies/{COMPANY_ID}/users/{TARGET_USER_ID}",
                json={"role": "editor"},
            )

        assert res.status_code == 502
        assert "取り消されました" in res.json()["detail"]
        assert mock_db.auth.admin.update_user_by_id.call_count == 1
        # update が2回（本更新 + 巻き戻し）
        assert mock_db.table.return_value.update.call_count == 2

    def test_patch_department_only_no_auth_sync(self):
        """role を変えない更新では Auth を呼ばない。"""
        client = _make_client()
        mock_db = MagicMock()
        chain = MagicMock()
        mock_db.table.return_value = chain
        for name in ("select", "eq", "update"):
            getattr(chain, name).return_value = chain

        row = _updated_row("admin")
        row["department"] = "営業"

        exec_queue = [
            MagicMock(data=[{"id": TARGET_USER_ID, "role": "admin"}]),
            MagicMock(data=[row]),
        ]

        def _ex(*_a, **_k):
            return exec_queue.pop(0)

        chain.execute.side_effect = _ex
        mock_db.auth = MagicMock()

        with patch("routers.users.get_service_client", return_value=mock_db), patch(
            "routers.users.audit_log", new_callable=AsyncMock
        ):
            res = client.patch(
                f"/companies/{COMPANY_ID}/users/{TARGET_USER_ID}",
                json={"department": "営業"},
            )

        assert res.status_code == 200
        mock_db.auth.admin.update_user_by_id.assert_not_called()

"""Tests for routers/mfa.py — SOC2準備 MFAエンドポイント。

全テストはSupabaseとpyotpをモックし、DB接続なしで実行する。
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.mfa import router

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

CURRENT_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="test@example.com",
)

MOCK_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # テスト用固定シークレット


def _make_client() -> TestClient:
    """テスト用FastAPIクライアントを生成する。"""
    from auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CURRENT_USER
    return TestClient(app, raise_server_exceptions=False)


def _mock_db_chain(select_data=None, maybe_single_data=None) -> MagicMock:
    """Supabase DBチェーンのモックを生成する。"""
    db = MagicMock()
    chain = MagicMock()
    db.table.return_value = chain
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.delete.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain

    if maybe_single_data is not None:
        chain.execute.return_value = MagicMock(data=maybe_single_data)
    elif select_data is not None:
        chain.execute.return_value = MagicMock(data=select_data)
    else:
        chain.execute.return_value = MagicMock(data=None)

    return db


# ============================================================
# GET /mfa/status
# ============================================================

class TestMFAStatus:
    def test_mfa_status_returns_disabled_by_default(self):
        """MFA設定レコードがない場合、is_enabled=falseを返す。"""
        client = _make_client()

        with patch("routers.mfa.get_service_client") as mock_get_db:
            db = _mock_db_chain(maybe_single_data=None)
            mock_get_db.return_value = db

            resp = client.get("/mfa/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_enabled"] is False
        assert body["last_verified_at"] is None

    def test_mfa_status_returns_enabled_when_set(self):
        """MFA有効レコードがある場合、is_enabled=trueと最終確認日時を返す。"""
        client = _make_client()
        verified_at = datetime.now(timezone.utc).isoformat()

        with patch("routers.mfa.get_service_client") as mock_get_db:
            db = _mock_db_chain(maybe_single_data={
                "is_enabled": True,
                "last_verified_at": verified_at,
            })
            mock_get_db.return_value = db

            resp = client.get("/mfa/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_enabled"] is True
        assert body["last_verified_at"] is not None


# ============================================================
# POST /mfa/setup
# ============================================================

class TestMFASetup:
    def test_mfa_setup_returns_secret_and_qr(self):
        """セットアップエンドポイントがtotp_secretとqr_code_urlを返す。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa._generate_totp_secret", return_value=MOCK_TOTP_SECRET),
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            # 既存レコードなし（新規作成ケース）
            db = _mock_db_chain(maybe_single_data=None)
            mock_get_db.return_value = db

            resp = client.post("/mfa/setup")

        assert resp.status_code == 200
        body = resp.json()
        assert "totp_secret" in body
        assert "qr_code_url" in body
        assert body["totp_secret"] == MOCK_TOTP_SECRET
        # otpauth:// スキームであること
        assert body["qr_code_url"].startswith("otpauth://totp/")

    def test_mfa_setup_updates_existing_record(self):
        """既存レコードがある場合はUPDATEが呼ばれる（再セットアップ）。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa._generate_totp_secret", return_value=MOCK_TOTP_SECRET),
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            # 既存レコードあり
            db = _mock_db_chain(maybe_single_data={"id": str(uuid.uuid4())})
            mock_get_db.return_value = db

            resp = client.post("/mfa/setup")

        assert resp.status_code == 200
        body = resp.json()
        assert body["totp_secret"] == MOCK_TOTP_SECRET


# ============================================================
# POST /mfa/verify
# ============================================================

class TestMFAVerify:
    def test_mfa_verify_success(self):
        """正しいTOTPコードを渡すと success=true が返る。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa._verify_totp_code", return_value=True),
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            db = _mock_db_chain(maybe_single_data={
                "totp_secret": MOCK_TOTP_SECRET,
                "is_enabled": False,
            })
            mock_get_db.return_value = db

            resp = client.post("/mfa/verify", json={"totp_code": "123456"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_mfa_verify_invalid_code_returns_400(self):
        """誤ったTOTPコードを渡すと 400 が返る。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa._verify_totp_code", return_value=False),
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            db = _mock_db_chain(maybe_single_data={
                "totp_secret": MOCK_TOTP_SECRET,
                "is_enabled": False,
            })
            mock_get_db.return_value = db

            resp = client.post("/mfa/verify", json={"totp_code": "000000"})

        assert resp.status_code == 400
        body = resp.json()
        assert "正しくありません" in body["detail"]

    def test_mfa_verify_without_setup_returns_400(self):
        """セットアップ前にverifyを呼ぶと 400 が返る。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            # レコードなし
            db = _mock_db_chain(maybe_single_data=None)
            mock_get_db.return_value = db

            resp = client.post("/mfa/verify", json={"totp_code": "123456"})

        assert resp.status_code == 400
        body = resp.json()
        assert "セットアップ" in body["detail"]


# ============================================================
# DELETE /mfa/disable
# ============================================================

class TestMFADisable:
    def test_mfa_disable_success(self):
        """MFA有効時に無効化すると success=true が返る。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            db = _mock_db_chain(maybe_single_data={
                "id": str(uuid.uuid4()),
                "is_enabled": True,
            })
            mock_get_db.return_value = db

            resp = client.delete("/mfa/disable")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

    def test_mfa_disable_already_disabled(self):
        """すでに無効の場合も success=true を返す（冪等性）。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            db = _mock_db_chain(maybe_single_data={
                "id": str(uuid.uuid4()),
                "is_enabled": False,
            })
            mock_get_db.return_value = db

            resp = client.delete("/mfa/disable")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "すでに無効" in body["message"]

    def test_mfa_disable_not_found_returns_404(self):
        """MFA設定レコードが存在しない場合は 404 が返る。"""
        client = _make_client()

        with (
            patch("routers.mfa.get_service_client") as mock_get_db,
            patch("routers.mfa.log_audit", new_callable=AsyncMock),
        ):
            db = _mock_db_chain(maybe_single_data=None)
            mock_get_db.return_value = db

            resp = client.delete("/mfa/disable")

        assert resp.status_code == 404

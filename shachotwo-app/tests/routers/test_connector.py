"""Tests for routers/connector.py — SaaS connector management."""
import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.connector import router, _encrypt_credentials, _decrypt_credentials
from auth.jwt import JWTClaims
from auth.middleware import get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPANY_ID = str(uuid4())
USER_ID = str(uuid4())
CONNECTOR_ID = str(uuid4())

MOCK_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)

MOCK_ROW = {
    "id": CONNECTOR_ID,
    "tool_name": "kintone",
    "tool_type": "saas",
    "connection_method": "api",
    "health_status": "unknown",
    "last_health_check": None,
    "status": "active",
    "connection_config": {"_encrypted": _encrypt_credentials({"api_key": "test-key"})},
}


def _make_client(user: JWTClaims = MOCK_USER) -> TestClient:
    """Create a TestClient with get_current_user dependency overridden.

    require_role("admin") 内部で get_current_user を Depends しているため、
    get_current_user をオーバーライドすることで admin チェックを通過させる。
    """
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper: build a mock Supabase chain that returns given data
# ---------------------------------------------------------------------------

def _mock_db(data: list | None = None, count: int | None = None) -> MagicMock:
    """Return a mock Supabase client whose table().*.execute() returns data."""
    execute_result = MagicMock()
    execute_result.data = data if data is not None else []
    execute_result.count = count if count is not None else (len(data) if isinstance(data, list) else 0)

    chain = MagicMock()
    chain.execute.return_value = execute_result

    for method in ("select", "eq", "order", "insert", "update", "delete", "range"):
        getattr(chain, method).return_value = chain

    db = MagicMock()
    db.table.return_value = chain
    return db


# ---------------------------------------------------------------------------
# Tests: credential helpers
# ---------------------------------------------------------------------------

class TestCredentialHelpers:
    def test_encrypt_decrypt_roundtrip(self):
        creds = {"api_key": "secret-123", "domain": "example.kintone.com"}
        encoded = _encrypt_credentials(creds)
        assert isinstance(encoded, str)
        decoded = _decrypt_credentials(encoded)
        assert decoded == creds

    def test_encrypted_is_base64(self):
        """暗号化結果は base64 エンコードされたバイナリであり、平文 JSON ではない。
        AES-256-GCM 暗号化のため、base64 デコード後は nonce+ciphertext のバイナリ。
        """
        encoded = _encrypt_credentials({"token": "abc"})
        # base64 デコードできること（バイナリ）
        raw_bytes = base64.b64decode(encoded.encode())
        assert isinstance(raw_bytes, bytes)
        assert len(raw_bytes) > 12  # nonce(12bytes) + ciphertext
        # 平文 JSON がそのまま入っていないこと（暗号化されていること）
        assert b'"token"' not in raw_bytes


# ---------------------------------------------------------------------------
# Tests: POST /connectors — 接続登録
# ---------------------------------------------------------------------------

class TestCreateConnector:
    def test_create_connector_success(self):
        """接続登録が成功し、レスポンスに connection_config (credentials) が含まれない。"""
        client = _make_client()
        inserted_row = {**MOCK_ROW}

        with patch("routers.connector.get_service_client") as mock_get:
            mock_get.return_value = _mock_db(data=[inserted_row])

            resp = client.post(
                "/connectors",
                json={
                    "tool_name": "kintone",
                    "tool_type": "saas",
                    "connection_config": {"api_key": "test-key", "domain": "demo.kintone.com"},
                },
                headers={"Authorization": "Bearer dummy"},
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["tool_name"] == "kintone"
        assert body["tool_type"] == "saas"
        # credentials は返さない
        assert "connection_config" not in body
        assert "api_key" not in body

    def test_create_connector_saves_encrypted_credentials(self):
        """DB insert に渡されるデータに _encrypted キーが含まれる（平文なし）。"""
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: MOCK_USER
        client = TestClient(app, raise_server_exceptions=False)

        inserted_row = {**MOCK_ROW}
        db = _mock_db(data=[inserted_row])

        with patch("routers.connector.get_service_client", return_value=db):
            client.post(
                "/connectors",
                json={
                    "tool_name": "freee",
                    "tool_type": "api",
                    "connection_config": {"client_id": "id-123", "client_secret": "sec-456"},
                },
                headers={"Authorization": "Bearer dummy"},
            )

        # insert() が呼ばれた引数を確認
        insert_call = db.table.return_value.insert.call_args
        assert insert_call is not None, "insert() が呼ばれていない"
        inserted_payload = insert_call[0][0]
        assert "_encrypted" in inserted_payload["connection_config"]
        # 平文の credentials が漏れていないこと
        assert "client_secret" not in inserted_payload["connection_config"]

    def test_create_connector_invalid_tool_type(self):
        """無効な tool_type は 422 を返す。"""
        client = _make_client()

        resp = client.post(
            "/connectors",
            json={
                "tool_name": "unknown-tool",
                "tool_type": "invalid_type",
                "connection_config": {"api_key": "x"},
            },
            headers={"Authorization": "Bearer dummy"},
        )

        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Tests: GET /connectors — 一覧取得
# ---------------------------------------------------------------------------

class TestListConnectors:
    def test_list_connectors_excludes_credentials(self):
        """一覧取得で credentials (connection_config) が返らない。"""
        client = _make_client()
        rows = [
            {**MOCK_ROW, "id": str(uuid4()), "tool_name": "kintone"},
            {**MOCK_ROW, "id": str(uuid4()), "tool_name": "freee"},
        ]

        with patch("routers.connector.get_service_client") as mock_get:
            mock_get.return_value = _mock_db(data=rows, count=2)

            resp = client.get("/connectors", headers={"Authorization": "Bearer dummy"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

        for item in body["items"]:
            assert "connection_config" not in item
            assert "api_key" not in item
            assert "_encrypted" not in item

    def test_list_connectors_empty(self):
        """接続が 0 件でも 200 を返す。"""
        client = _make_client()

        with patch("routers.connector.get_service_client") as mock_get:
            mock_get.return_value = _mock_db(data=[], count=0)

            resp = client.get("/connectors", headers={"Authorization": "Bearer dummy"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Tests: DELETE /connectors/{id} — 削除
# ---------------------------------------------------------------------------

class TestDeleteConnector:
    def _make_two_chain_db(self, select_data: list, update_data: list) -> MagicMock:
        """1回目 table() は select 用、2回目は update 用のチェーンを返す mock DB。"""
        db = MagicMock()

        select_chain = MagicMock()
        select_result = MagicMock()
        select_result.data = select_data
        select_chain.execute.return_value = select_result
        for method in ("select", "eq", "order", "range"):
            getattr(select_chain, method).return_value = select_chain

        update_chain = MagicMock()
        update_result = MagicMock()
        update_result.data = update_data
        update_chain.execute.return_value = update_result
        for method in ("update", "eq"):
            getattr(update_chain, method).return_value = update_chain

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            return select_chain if call_count["n"] == 1 else update_chain

        db.table.side_effect = table_side_effect
        return db

    def test_delete_connector_success(self):
        """削除が成功すると 204 を返す。"""
        client = _make_client()
        db = self._make_two_chain_db(
            select_data=[{"id": CONNECTOR_ID}],
            update_data=[{"id": CONNECTOR_ID, "status": "deleted"}],
        )

        with patch("routers.connector.get_service_client", return_value=db):
            resp = client.delete(
                f"/connectors/{CONNECTOR_ID}",
                headers={"Authorization": "Bearer dummy"},
            )

        assert resp.status_code == 204, resp.text

    def test_delete_connector_not_found(self):
        """存在しない connector_id は 404 を返す。"""
        client = _make_client()

        with patch("routers.connector.get_service_client") as mock_get:
            mock_get.return_value = _mock_db(data=[], count=0)

            resp = client.delete(
                f"/connectors/{uuid4()}",
                headers={"Authorization": "Bearer dummy"},
            )

        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Tests: POST /connectors/{id}/test — ヘルスチェック
# ---------------------------------------------------------------------------

class TestTestConnector:
    def _make_health_db(self, select_data: list, updated_row: dict) -> MagicMock:
        db = MagicMock()

        select_chain = MagicMock()
        select_result = MagicMock()
        select_result.data = select_data
        select_chain.execute.return_value = select_result
        for method in ("select", "eq", "order", "range"):
            getattr(select_chain, method).return_value = select_chain

        update_chain = MagicMock()
        update_result = MagicMock()
        update_result.data = [updated_row]
        update_chain.execute.return_value = update_result
        for method in ("update", "eq"):
            getattr(update_chain, method).return_value = update_chain

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            return select_chain if call_count["n"] == 1 else update_chain

        db.table.side_effect = table_side_effect
        return db

    def test_health_check_returns_healthy(self):
        """ヘルスチェックで health_status='healthy' が返る。"""
        client = _make_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        updated_row = {**MOCK_ROW, "health_status": "healthy", "last_health_check": now_iso}
        db = self._make_health_db(select_data=[MOCK_ROW], updated_row=updated_row)

        with patch("routers.connector.get_service_client", return_value=db):
            resp = client.post(
                f"/connectors/{CONNECTOR_ID}/test",
                headers={"Authorization": "Bearer dummy"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["health_status"] == "healthy"
        assert body["last_health_check"] is not None
        assert body["tool_name"] == "kintone"

    def test_health_check_not_found(self):
        """存在しない connector_id は 404 を返す。"""
        client = _make_client()

        with patch("routers.connector.get_service_client") as mock_get:
            mock_get.return_value = _mock_db(data=[], count=0)

            resp = client.post(
                f"/connectors/{uuid4()}/test",
                headers={"Authorization": "Bearer dummy"},
            )

        assert resp.status_code == 404, resp.text

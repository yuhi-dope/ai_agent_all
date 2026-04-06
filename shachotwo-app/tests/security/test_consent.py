"""Tests for security.consent — 同意管理モジュール。

テスト対象:
- grant_consent: DB にレコードが作成される
- check_consent: アクティブな同意に True を返す
- revoke 後に check_consent が False を返す
- 有効期限切れの同意に False を返す
- delete_all_user_data が関連データを削除する
- API エンドポイント正常系 (grant/revoke/status)
- 無効な consent_type で 422
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from security.consent import (
    CONSENT_TYPES,
    ConsentRecord,
    _is_active,
    _row_to_model,
    check_consent,
    delete_all_user_data,
    get_user_consents,
    grant_consent,
    revoke_consent,
)

# ─────────────────────────────────────────────────────────────
# 共通フィクスチャ
# ─────────────────────────────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
NOW = datetime.now(timezone.utc)


def _make_row(
    consent_type: str = "data_processing",
    revoked_at: Any = None,
    expires_at: Any = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """テスト用 DB 行を生成する。"""
    return {
        "id": record_id or str(uuid.uuid4()),
        "company_id": COMPANY_ID,
        "user_id": USER_ID,
        "consent_type": consent_type,
        "granted_at": NOW.isoformat(),
        "revoked_at": revoked_at,
        "expires_at": expires_at,
        "consent_version": "1.0",
    }


def _mock_db_with_rows(rows: list[dict]) -> MagicMock:
    """select/insert/update クエリチェーンをモックする。"""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.data = rows

    chain = mock_db.table.return_value
    for method in ("select", "insert", "update", "delete",
                   "eq", "is_", "order", "execute"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    return mock_db


# ─────────────────────────────────────────────────────────────
# _is_active のユニットテスト
# ─────────────────────────────────────────────────────────────

class TestIsActive:
    def test_active_no_expiry(self):
        """revoked_at=None、expires_at=None → アクティブ。"""
        row = _make_row()
        assert _is_active(row) is True

    def test_revoked(self):
        """revoked_at が設定されている → 非アクティブ。"""
        row = _make_row(revoked_at=NOW.isoformat())
        assert _is_active(row) is False

    def test_expired(self):
        """expires_at が過去 → 非アクティブ。"""
        past = (NOW - timedelta(days=1)).isoformat()
        row = _make_row(expires_at=past)
        assert _is_active(row) is False

    def test_future_expiry(self):
        """expires_at が未来 → アクティブ。"""
        future = (NOW + timedelta(days=30)).isoformat()
        row = _make_row(expires_at=future)
        assert _is_active(row) is True

    def test_expires_at_z_suffix(self):
        """Z サフィックス付き ISO 文字列でも正しく処理する。"""
        future = (NOW + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row = _make_row(expires_at=future)
        assert _is_active(row) is True


# ─────────────────────────────────────────────────────────────
# _row_to_model のユニットテスト
# ─────────────────────────────────────────────────────────────

class TestRowToModel:
    def test_basic_conversion(self):
        row = _make_row()
        model = _row_to_model(row)
        assert isinstance(model, ConsentRecord)
        assert model.company_id == COMPANY_ID
        assert model.user_id == USER_ID
        assert model.consent_type == "data_processing"
        assert model.is_active is True

    def test_revoked_conversion(self):
        row = _make_row(revoked_at=NOW.isoformat())
        model = _row_to_model(row)
        assert model.is_active is False
        assert model.revoked_at is not None


# ─────────────────────────────────────────────────────────────
# grant_consent
# ─────────────────────────────────────────────────────────────

class TestGrantConsent:
    @pytest.mark.asyncio
    async def test_grant_creates_record(self):
        """grant_consent が consent_records に INSERT し ConsentRecord を返す。"""
        row = _make_row(consent_type="knowledge_collection")
        mock_db = _mock_db_with_rows([row])

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            record = await grant_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="knowledge_collection",
            )

        assert isinstance(record, ConsentRecord)
        assert record.consent_type == "knowledge_collection"
        assert record.company_id == COMPANY_ID
        # INSERT が呼ばれたことを確認
        mock_db.table.assert_called_with("consent_records")

    @pytest.mark.asyncio
    async def test_grant_with_ip_and_user_agent(self):
        """ip_address と user_agent が INSERT ペイロードに含まれる。"""
        row = _make_row(consent_type="data_processing")
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [row]

        insert_chain = MagicMock()
        insert_chain.execute.return_value = mock_result

        table_chain = MagicMock()
        table_chain.insert.return_value = insert_chain
        mock_db.table.return_value = table_chain

        captured: dict[str, Any] = {}

        def _capture_insert(payload: dict) -> MagicMock:
            captured.update(payload)
            return insert_chain

        table_chain.insert.side_effect = _capture_insert

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            await grant_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="data_processing",
                ip_address="192.168.0.1",
                user_agent="Mozilla/5.0",
            )

        assert captured.get("ip_address") == "192.168.0.1"
        assert captured.get("user_agent") == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_grant_invalid_type_raises(self):
        """不正な consent_type は ValueError を送出する。"""
        with pytest.raises(ValueError, match="無効な consent_type"):
            await grant_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="invalid_type",
            )

    @pytest.mark.asyncio
    async def test_grant_with_expires_at(self):
        """expires_at が INSERT ペイロードに含まれる。"""
        future = NOW + timedelta(days=365)
        row = _make_row(consent_type="benchmark_sharing", expires_at=future.isoformat())
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.data = [row]

        captured: dict[str, Any] = {}
        insert_chain = MagicMock()
        insert_chain.execute.return_value = mock_result
        table_chain = MagicMock()
        table_chain.insert.side_effect = lambda p: [captured.update(p), insert_chain][1]
        mock_db.table.return_value = table_chain

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            record = await grant_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="benchmark_sharing",
                expires_at=future,
            )

        assert "expires_at" in captured
        assert record.expires_at is not None


# ─────────────────────────────────────────────────────────────
# check_consent
# ─────────────────────────────────────────────────────────────

class TestCheckConsent:
    @pytest.mark.asyncio
    async def test_active_consent_returns_true(self):
        """有効な同意レコードが存在する場合 True を返す。"""
        row = _make_row(consent_type="data_processing", expires_at=None)
        mock_db = _mock_db_with_rows([row])

        with patch("security.consent.get_service_client", return_value=mock_db):
            result = await check_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="data_processing",
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_no_consent_returns_false(self):
        """レコードが存在しない場合 False を返す。"""
        mock_db = _mock_db_with_rows([])

        with patch("security.consent.get_service_client", return_value=mock_db):
            result = await check_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="data_processing",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_expired_consent_returns_false(self):
        """expires_at が過去のレコードのみの場合 False を返す。"""
        past = (NOW - timedelta(days=1)).isoformat()
        row = _make_row(consent_type="behavior_inference", expires_at=past)
        mock_db = _mock_db_with_rows([row])

        with patch("security.consent.get_service_client", return_value=mock_db):
            result = await check_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="behavior_inference",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_future_expiry_returns_true(self):
        """expires_at が未来のレコードがある場合 True を返す。"""
        future = (NOW + timedelta(days=30)).isoformat()
        row = _make_row(consent_type="benchmark_sharing", expires_at=future)
        mock_db = _mock_db_with_rows([row])

        with patch("security.consent.get_service_client", return_value=mock_db):
            result = await check_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="benchmark_sharing",
            )

        assert result is True


# ─────────────────────────────────────────────────────────────
# revoke_consent
# ─────────────────────────────────────────────────────────────

class TestRevokeConsent:
    @pytest.mark.asyncio
    async def test_revoke_existing_consent_returns_true(self):
        """有効な同意を revoke すると True を返す。"""
        row = _make_row(consent_type="data_processing")
        mock_db = _mock_db_with_rows([row])

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            result = await revoke_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="data_processing",
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_consent_returns_false(self):
        """同意レコードがない場合 False を返す。"""
        mock_db = _mock_db_with_rows([])

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            result = await revoke_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="data_processing",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_invalid_type_raises(self):
        """不正な consent_type は ValueError を送出する。"""
        with pytest.raises(ValueError):
            await revoke_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="unknown_type",
            )

    @pytest.mark.asyncio
    async def test_check_consent_false_after_revoke(self):
        """revoke 後に check_consent が False を返す（ロジックの結合確認）。

        revoke 後は check_consent の DB クエリが空を返すシナリオをシミュレートする。
        """
        # check_consent の DB クエリは revoked_at IS NULL を条件にするため
        # revoke 後は空リストが返るはず
        mock_db_check = _mock_db_with_rows([])

        with patch("security.consent.get_service_client", return_value=mock_db_check):
            result = await check_consent(
                company_id=COMPANY_ID,
                user_id=USER_ID,
                consent_type="knowledge_collection",
            )

        assert result is False


# ─────────────────────────────────────────────────────────────
# get_user_consents
# ─────────────────────────────────────────────────────────────

class TestGetUserConsents:
    @pytest.mark.asyncio
    async def test_returns_all_records(self):
        """全同意レコードをリストで返す。"""
        rows = [
            _make_row(consent_type="data_processing"),
            _make_row(consent_type="knowledge_collection",
                      revoked_at=NOW.isoformat()),
        ]
        mock_db = _mock_db_with_rows(rows)

        with patch("security.consent.get_service_client", return_value=mock_db):
            records = await get_user_consents(
                company_id=COMPANY_ID,
                user_id=USER_ID,
            )

        assert len(records) == 2
        assert all(isinstance(r, ConsentRecord) for r in records)

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self):
        """レコードがない場合は空リストを返す。"""
        mock_db = _mock_db_with_rows([])

        with patch("security.consent.get_service_client", return_value=mock_db):
            records = await get_user_consents(
                company_id=COMPANY_ID,
                user_id=USER_ID,
            )

        assert records == []


# ─────────────────────────────────────────────────────────────
# delete_all_user_data
# ─────────────────────────────────────────────────────────────

class TestDeleteAllUserData:
    @pytest.mark.asyncio
    async def test_deletes_sessions_and_revokes_consents(self):
        """knowledge_sessions の削除と consent_records の revoke が行われる。"""
        session_rows = [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}]
        consent_rows = [_make_row(), _make_row(consent_type="behavior_inference")]

        call_count = 0

        def _side_effect(table_name: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            chain = MagicMock()
            if table_name == "knowledge_sessions":
                chain.delete.return_value = chain
                chain.eq.return_value = chain
                result = MagicMock()
                result.data = session_rows
                chain.execute.return_value = result
            else:
                # consent_records
                chain.update.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                result = MagicMock()
                result.data = consent_rows
                chain.execute.return_value = result
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = _side_effect

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            result = await delete_all_user_data(
                company_id=COMPANY_ID,
                user_id=USER_ID,
            )

        assert result["deleted_sessions"] == len(session_rows)
        assert result["revoked_consents"] == len(consent_rows)

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_data(self):
        """データがない場合 deleted_sessions=0、revoked_consents=0。"""
        def _empty_table(table_name: str) -> MagicMock:
            chain = MagicMock()
            chain.delete.return_value = chain
            chain.update.return_value = chain
            chain.eq.return_value = chain
            chain.is_.return_value = chain
            result = MagicMock()
            result.data = []
            chain.execute.return_value = result
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = _empty_table

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            result = await delete_all_user_data(
                company_id=COMPANY_ID,
                user_id=USER_ID,
            )

        assert result["deleted_sessions"] == 0
        assert result["revoked_consents"] == 0


# ─────────────────────────────────────────────────────────────
# API エンドポイントテスト (TestClient)
# ─────────────────────────────────────────────────────────────

# テスト用 FastAPI アプリ
from routers.consent import router as consent_router
from auth.middleware import get_current_user

_test_user = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="editor",
    email="user@example.com",
)

_app = FastAPI()
_app.include_router(consent_router)
_app.dependency_overrides[get_current_user] = lambda: _test_user

_client = TestClient(_app, raise_server_exceptions=False)


class TestConsentGrantEndpoint:
    def test_grant_valid_consent_type(self):
        """正常な consent_type で 201 が返る。"""
        row = _make_row(consent_type="data_processing")

        with (
            patch("security.consent.get_service_client",
                  return_value=_mock_db_with_rows([row])),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            resp = _client.post(
                "/consent/grant",
                json={"consent_type": "data_processing"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert "record" in data
        assert "message" in data
        assert data["record"]["consent_type"] == "data_processing"

    def test_grant_invalid_consent_type_returns_422(self):
        """無効な consent_type で 422 が返る。"""
        resp = _client.post(
            "/consent/grant",
            json={"consent_type": "totally_invalid_type"},
        )
        assert resp.status_code == 422

    def test_grant_with_version(self):
        """version フィールドを指定しても正常に動作する。"""
        row = _make_row(consent_type="knowledge_collection")

        with (
            patch("security.consent.get_service_client",
                  return_value=_mock_db_with_rows([row])),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            resp = _client.post(
                "/consent/grant",
                json={"consent_type": "knowledge_collection", "version": "2.0"},
            )

        assert resp.status_code == 201


class TestConsentRevokeEndpoint:
    def test_revoke_existing_consent(self):
        """有効な同意を revoke すると revoked=True。"""
        row = _make_row(consent_type="benchmark_sharing")

        with (
            patch("security.consent.get_service_client",
                  return_value=_mock_db_with_rows([row])),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            resp = _client.post(
                "/consent/revoke",
                json={"consent_type": "benchmark_sharing"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["revoked"] is True
        assert data["consent_type"] == "benchmark_sharing"

    def test_revoke_nonexistent_consent(self):
        """同意レコードがない場合も 200 で revoked=False。"""
        with (
            patch("security.consent.get_service_client",
                  return_value=_mock_db_with_rows([])),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            resp = _client.post(
                "/consent/revoke",
                json={"consent_type": "data_portability"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["revoked"] is False

    def test_revoke_invalid_type_returns_422(self):
        """無効な consent_type で 422 が返る。"""
        resp = _client.post(
            "/consent/revoke",
            json={"consent_type": "no_such_type"},
        )
        assert resp.status_code == 422


class TestConsentStatusEndpoint:
    def test_status_returns_all_consents(self):
        """GET /consent/status が consents と active_types を返す。"""
        rows = [
            _make_row(consent_type="data_processing"),
            _make_row(consent_type="knowledge_collection",
                      revoked_at=NOW.isoformat()),
        ]
        mock_db = _mock_db_with_rows(rows)

        with patch("security.consent.get_service_client", return_value=mock_db):
            resp = _client.get("/consent/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "consents" in data
        assert "active_types" in data
        assert len(data["consents"]) == 2
        # revoked は active_types に含まれない
        assert "knowledge_collection" not in data["active_types"]
        assert "data_processing" in data["active_types"]

    def test_status_empty(self):
        """同意レコードがない場合、空リストを返す。"""
        with patch("security.consent.get_service_client",
                   return_value=_mock_db_with_rows([])):
            resp = _client.get("/consent/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["consents"] == []
        assert data["active_types"] == []


class TestDeleteAllMyDataEndpoint:
    def test_delete_all_data(self):
        """DELETE /consent/all-my-data が正常に動作する。"""
        session_rows = [{"id": str(uuid.uuid4())}]
        consent_rows = [_make_row()]

        def _table_side_effect(table_name: str) -> MagicMock:
            chain = MagicMock()
            chain.delete.return_value = chain
            chain.update.return_value = chain
            chain.eq.return_value = chain
            chain.is_.return_value = chain
            result = MagicMock()
            result.data = session_rows if table_name == "knowledge_sessions" else consent_rows
            chain.execute.return_value = result
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = _table_side_effect

        with (
            patch("security.consent.get_service_client", return_value=mock_db),
            patch("security.consent.audit_log", new_callable=AsyncMock),
        ):
            resp = _client.request("DELETE", "/consent/all-my-data")

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted_sessions"] == 1
        assert data["revoked_consents"] == 1
        assert "削除が完了" in data["message"]


# ─────────────────────────────────────────────────────────────
# CONSENT_TYPES 定数のテスト
# ─────────────────────────────────────────────────────────────

class TestConsentTypes:
    def test_all_required_types_present(self):
        """仕様書に記載の7種類の consent_type が定義されている。"""
        required = {
            "knowledge_collection",
            "data_processing",
            "behavior_inference",
            "benchmark_sharing",
            "right_to_deletion",
            "data_portability",
            "partner_data_sharing",
        }
        assert required.issubset(set(CONSENT_TYPES))

    def test_no_duplicates(self):
        """重複がない。"""
        assert len(CONSENT_TYPES) == len(set(CONSENT_TYPES))

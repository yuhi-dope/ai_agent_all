"""Tests for routers/ingestion.py — CSV template / preview / import endpoints."""
import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
import routers.ingestion as ingestion_module
from routers.ingestion import (
    router,
    CSV_TEMPLATES,
    _validate_csv_rows,
    _row_to_knowledge_content,
)

# ─────────────────────────────────────
# 共通フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

ANY_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="editor",
    email="user@example.com",
)

ADMIN_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)


def _make_client_any_user() -> TestClient:
    """editor ユーザーで認証済みクライアントを返す。"""
    from auth.middleware import get_current_user, require_role
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: ANY_USER
    app.dependency_overrides[require_role("admin")] = lambda: ANY_USER
    return TestClient(app, raise_server_exceptions=False)


def _make_client_admin() -> TestClient:
    """admin ユーザーで認証済みクライアントを返す。"""
    from auth.middleware import get_current_user, require_role
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    app.dependency_overrides[require_role("admin")] = lambda: ADMIN_USER
    return TestClient(app, raise_server_exceptions=False)


def _make_csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    """テスト用CSVバイト列を生成する（UTF-8）。"""
    import csv
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


# ─────────────────────────────────────
# ユニットテスト: _validate_csv_rows
# ─────────────────────────────────────

class TestValidateCsvRows:
    def test_valid_products_rows_return_no_errors(self):
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "1500", "5", "100", ""]]
        errors = _validate_csv_rows(headers, rows, "products")
        assert errors == []

    def test_non_numeric_price_returns_error(self):
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "千五百円", "5", "100", ""]]
        errors = _validate_csv_rows(headers, rows, "products")
        assert len(errors) == 1
        assert errors[0].column == "単価(円)"
        assert errors[0].row == 2

    def test_comma_formatted_number_passes(self):
        """1,500 のようなカンマ区切り数値はOK。"""
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "1,500", "5", "100", ""]]
        errors = _validate_csv_rows(headers, rows, "products")
        assert errors == []

    def test_empty_numeric_cell_passes(self):
        """数値カラムが空のときはエラーにしない。"""
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "", "5", "100", ""]]
        errors = _validate_csv_rows(headers, rows, "products")
        assert errors == []

    def test_multiple_errors_on_multiple_rows(self):
        headers = ["品番", "材料費(円)", "加工費(円)", "外注費(円)", "経費(円)", "合計原価(円)", "備考"]
        rows = [
            ["MT-001", "abc", "800", "0", "200", "xxx", ""],
            ["MT-002", "500", "800", "0", "200", "1500", ""],
        ]
        errors = _validate_csv_rows(headers, rows, "cost_data")
        assert len(errors) == 2
        error_cols = {e.column for e in errors}
        assert "材料費(円)" in error_cols
        assert "合計原価(円)" in error_cols


# ─────────────────────────────────────
# ユニットテスト: _row_to_knowledge_content
# ─────────────────────────────────────

class TestRowToKnowledgeContent:
    def test_basic_conversion(self):
        headers = ["品番", "品名", "単価(円)"]
        row = ["MT-001", "フランジ", "1500"]
        content = _row_to_knowledge_content(headers, row, {}, "products")
        assert "品番: MT-001" in content
        assert "品名: フランジ" in content
        assert "単価(円): 1500" in content

    def test_column_mapping_applied(self):
        headers = ["品番", "単価(円)"]
        row = ["MT-001", "1500"]
        mapping = {"単価(円)": "unit_price"}
        content = _row_to_knowledge_content(headers, row, mapping, "products")
        assert "unit_price: 1500" in content

    def test_empty_values_omitted(self):
        headers = ["品番", "備考"]
        row = ["MT-001", ""]
        content = _row_to_knowledge_content(headers, row, {}, "products")
        assert "備考" not in content
        assert "品番: MT-001" in content


# ─────────────────────────────────────
# エンドポイントテスト: GET /ingestion/csv-template
# ─────────────────────────────────────

class TestDownloadCsvTemplate:
    def test_download_products_template(self):
        client = _make_client_any_user()
        response = client.get("/ingestion/csv-template?category=products")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "content-disposition" in response.headers
        # BOM付きUTF-8: ヘッダー行に品番が含まれる
        text = response.content.decode("utf-8-sig")
        assert "品番" in text
        assert "品名" in text

    def test_download_equipment_template(self):
        client = _make_client_any_user()
        response = client.get("/ingestion/csv-template?category=equipment")
        assert response.status_code == 200
        text = response.content.decode("utf-8-sig")
        assert "設備名" in text

    def test_invalid_category_returns_400(self):
        client = _make_client_any_user()
        response = client.get("/ingestion/csv-template?category=invalid_cat")
        assert response.status_code == 400

    def test_all_template_categories_available(self):
        """定義済み全カテゴリがダウンロード可能。"""
        client = _make_client_any_user()
        for category in CSV_TEMPLATES:
            response = client.get(f"/ingestion/csv-template?category={category}")
            assert response.status_code == 200, f"category={category} failed"

    def test_content_disposition_contains_filename(self):
        """Content-Disposition に filename が含まれる（日本語はRFC5987形式）。"""
        client = _make_client_any_user()
        response = client.get("/ingestion/csv-template?category=suppliers")
        assert response.status_code == 200
        cd = response.headers["content-disposition"]
        assert "filename" in cd
        assert "attachment" in cd


# ─────────────────────────────────────
# エンドポイントテスト: POST /ingestion/csv/preview
# ─────────────────────────────────────

class TestPreviewCsv:
    def test_preview_valid_csv(self):
        client = _make_client_any_user()
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [
            ["MT-001", "フランジ", "SUS304", "1500", "5", "100", ""],
            ["MT-002", "シャフト", "S45C", "3200", "7", "50", "熱処理あり"],
        ]
        csv_bytes = _make_csv_bytes(headers, rows)

        response = client.post(
            "/ingestion/csv/preview?category=products",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["columns"] == headers
        assert data["total_rows"] == 2
        assert data["valid_rows"] == 2
        assert data["errors"] == []
        assert "session_id" in data
        assert len(data["rows"]) == 2

    def test_preview_returns_validation_errors(self):
        client = _make_client_any_user()
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "千五百円", "5", "100", ""]]
        csv_bytes = _make_csv_bytes(headers, rows)

        response = client.post(
            "/ingestion/csv/preview?category=products",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_rows"] == 1
        assert data["valid_rows"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["column"] == "単価(円)"

    def test_preview_rows_capped_at_10(self):
        client = _make_client_any_user()
        headers = ["品番", "品名", "単価(円)"]
        rows = [[f"MT-{i:03d}", f"部品{i}", str(i * 100)] for i in range(1, 16)]
        csv_bytes = _make_csv_bytes(headers, rows)

        response = client.post(
            "/ingestion/csv/preview?category=products",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_rows"] == 15
        assert len(data["rows"]) == 10  # プレビューは先頭10行

    def test_preview_empty_file_returns_422(self):
        client = _make_client_any_user()
        response = client.post(
            "/ingestion/csv/preview?category=products",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 422

    def test_preview_stores_session_for_later_import(self):
        client = _make_client_any_user()
        headers = ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"]
        rows = [["MT-001", "フランジ", "SUS304", "1500", "5", "100", ""]]
        csv_bytes = _make_csv_bytes(headers, rows)

        response = client.post(
            "/ingestion/csv/preview?category=products",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )

        assert response.status_code == 200
        session_id = response.json()["session_id"]
        # モジュールレベルの _CSV_PREVIEW_STORE に保存されている
        assert session_id in ingestion_module._CSV_PREVIEW_STORE
        assert ingestion_module._CSV_PREVIEW_STORE[session_id]["company_id"] == COMPANY_ID


# ─────────────────────────────────────
# エンドポイントテスト: POST /ingestion/csv/import
# ─────────────────────────────────────

class TestImportCsv:
    def _seed_session(self, session_id: str, rows: list[list[str]] | None = None) -> None:
        """テスト用にモジュールの _CSV_PREVIEW_STORE に直接データを投入する。"""
        ingestion_module._CSV_PREVIEW_STORE[session_id] = {
            "headers": ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"],
            "rows": rows or [
                ["MT-001", "フランジ", "SUS304", "1500", "5", "100", ""],
                ["MT-002", "シャフト", "S45C", "3200", "7", "50", "熱処理あり"],
            ],
            "category": "products",
            "company_id": COMPANY_ID,
        }

    def test_import_inserts_knowledge_items(self):
        client = _make_client_admin()
        session_id = str(uuid.uuid4())
        self._seed_session(session_id)

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("routers.ingestion.get_service_client", return_value=mock_db):
            response = client.post(
                "/ingestion/csv/import",
                json={
                    "session_id": session_id,
                    "column_mapping": {},
                    "category": "products",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == 0
        assert data["knowledge_items_created"] == 2
        mock_db.table.return_value.insert.assert_called_once()

    def test_import_skips_error_rows(self):
        """バリデーションエラーのある行はスキップされる。"""
        client = _make_client_admin()
        session_id = str(uuid.uuid4())
        self._seed_session(session_id, rows=[
            ["MT-001", "フランジ", "SUS304", "千五百円", "5", "100", ""],
            ["MT-002", "シャフト", "S45C", "3200", "7", "50", ""],
        ])

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("routers.ingestion.get_service_client", return_value=mock_db):
            response = client.post(
                "/ingestion/csv/import",
                json={
                    "session_id": session_id,
                    "column_mapping": {},
                    "category": "products",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 1
        assert data["skipped"] == 1
        assert data["errors"] == 1

    def test_import_session_not_found_returns_404(self):
        client = _make_client_admin()
        response = client.post(
            "/ingestion/csv/import",
            json={
                "session_id": str(uuid.uuid4()),
                "column_mapping": {},
                "category": "products",
            },
        )
        assert response.status_code == 404

    def test_import_wrong_company_returns_403(self):
        """別会社のセッションIDを使おうとすると403。"""
        client = _make_client_admin()
        session_id = str(uuid.uuid4())
        ingestion_module._CSV_PREVIEW_STORE[session_id] = {
            "headers": ["品番"],
            "rows": [["MT-001"]],
            "category": "products",
            "company_id": str(uuid.uuid4()),  # 別会社
        }

        response = client.post(
            "/ingestion/csv/import",
            json={
                "session_id": session_id,
                "column_mapping": {},
                "category": "products",
            },
        )
        assert response.status_code == 403

    def test_import_clears_session_after_use(self):
        """import後にセッションがストアから削除される。"""
        client = _make_client_admin()
        session_id = str(uuid.uuid4())
        self._seed_session(session_id)

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("routers.ingestion.get_service_client", return_value=mock_db):
            client.post(
                "/ingestion/csv/import",
                json={
                    "session_id": session_id,
                    "column_mapping": {},
                    "category": "products",
                },
            )

        assert session_id not in ingestion_module._CSV_PREVIEW_STORE

    def test_import_db_failure_returns_500(self):
        client = _make_client_admin()
        session_id = str(uuid.uuid4())
        self._seed_session(session_id)

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.side_effect = Exception("DB error")

        with patch("routers.ingestion.get_service_client", return_value=mock_db):
            response = client.post(
                "/ingestion/csv/import",
                json={
                    "session_id": session_id,
                    "column_mapping": {},
                    "category": "products",
                },
            )

        assert response.status_code == 500

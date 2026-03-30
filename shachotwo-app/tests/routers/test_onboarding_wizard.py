"""Tests for routers/onboarding.py — setup-wizard endpoint."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.onboarding import router, SetupWizardRequest, SetupWizardResponse

# ─────────────────────────────────────
# 共通フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

ADMIN_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)


def _make_client() -> TestClient:
    """admin ユーザーで認証済みクライアントを返す。"""
    from auth.middleware import get_current_user, require_role
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_role("admin")] = lambda: ADMIN_USER
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    return TestClient(app, raise_server_exceptions=False)


def _make_mock_db() -> MagicMock:
    mock_db = MagicMock()
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "onboarding_plan": "self"
    }
    return mock_db


def _make_apply_template_result(items_created: int = 45) -> MagicMock:
    result = MagicMock()
    result.template_id = "manufacturing"
    result.items_created = items_created
    result.departments = ["製造", "購買", "品質管理"]
    return result


# ─────────────────────────────────────
# 正常系テスト
# ─────────────────────────────────────

class TestSetupWizardHappyPath:
    def test_wizard_returns_success_with_template_applied(self):
        """正常系: テンプレート適用成功 → success=True, template_applied=True。"""
        client = _make_client()
        mock_db = _make_mock_db()
        apply_result = _make_apply_template_result(items_created=45)

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, return_value=apply_result):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "manufacturing",
                    "sub_industry": "金属加工",
                    "employee_range": "51-100",
                    "departments": ["製造", "購買"],
                    "data_import_method": "template",
                    "selected_pipelines": ["quoting", "quality_control"],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["template_applied"] is True
        assert data["knowledge_items_created"] == 45
        assert data["pipelines_enabled"] == ["quoting", "quality_control"]
        assert data["next_step"] == "first_qa"

    def test_wizard_next_step_csv_import(self):
        """data_import_method=csv のとき next_step=csv_import になる。"""
        client = _make_client()
        mock_db = _make_mock_db()
        apply_result = _make_apply_template_result()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, return_value=apply_result):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "manufacturing",
                    "sub_industry": "",
                    "employee_range": "10-50",
                    "departments": [],
                    "data_import_method": "csv",
                    "selected_pipelines": [],
                },
            )

        assert response.status_code == 200
        assert response.json()["next_step"] == "csv_import"

    def test_wizard_next_step_connector_setup(self):
        """data_import_method=connector のとき next_step=connector_setup になる。"""
        client = _make_client()
        mock_db = _make_mock_db()
        apply_result = _make_apply_template_result()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, return_value=apply_result):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "construction",
                    "sub_industry": "",
                    "employee_range": "101-200",
                    "departments": ["工事"],
                    "data_import_method": "connector",
                    "selected_pipelines": ["estimation"],
                },
            )

        assert response.status_code == 200
        assert response.json()["next_step"] == "connector_setup"

    def test_wizard_companies_table_updated(self):
        """industry / employee_range / enabled_pipelines が companies テーブルに書き込まれる。"""
        client = _make_client()
        mock_db = _make_mock_db()
        apply_result = _make_apply_template_result()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, return_value=apply_result):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "manufacturing",
                    "sub_industry": "",
                    "employee_range": "51-100",
                    "departments": [],
                    "data_import_method": "template",
                    "selected_pipelines": ["quoting"],
                },
            )

        assert response.status_code == 200
        # update が複数回呼ばれるため call_args_list から industry を含む呼び出しを探す
        all_update_payloads = [
            call[0][0]
            for call in mock_db.table.return_value.update.call_args_list
        ]
        companies_update = next(
            (p for p in all_update_payloads if "industry" in p), None
        )
        assert companies_update is not None, "companies テーブルへの industry 更新が見つかりません"
        assert companies_update["industry"] == "manufacturing"
        assert companies_update["enabled_pipelines"] == ["quoting"]


# ─────────────────────────────────────
# テンプレートが見つからない場合
# ─────────────────────────────────────

class TestSetupWizardTemplateNotFound:
    def test_wizard_succeeds_even_if_template_missing(self):
        """業種テンプレートが見つからなくても success=True（template_applied=False）で返る。"""
        client = _make_client()
        mock_db = _make_mock_db()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, side_effect=ValueError("Template not found")):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "unknown_industry",
                    "sub_industry": "",
                    "employee_range": "10-50",
                    "departments": [],
                    "data_import_method": "template",
                    "selected_pipelines": [],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["template_applied"] is False
        assert data["knowledge_items_created"] == 0


# ─────────────────────────────────────
# エラーハンドリング
# ─────────────────────────────────────

class TestSetupWizardDBError:
    def test_wizard_returns_500_on_companies_update_failure(self):
        """companies テーブル更新失敗時は 500 を返す。"""
        client = _make_client()
        mock_db = _make_mock_db()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.side_effect = Exception("DB error")
        apply_result = _make_apply_template_result()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, return_value=apply_result):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "manufacturing",
                    "sub_industry": "",
                    "employee_range": "51-100",
                    "departments": [],
                    "data_import_method": "template",
                    "selected_pipelines": [],
                },
            )

        assert response.status_code == 500

    def test_wizard_returns_500_on_template_exception(self):
        """テンプレート適用で予期せぬ例外が起きた場合は 500 を返す。"""
        client = _make_client()
        mock_db = _make_mock_db()

        with patch("routers.onboarding.get_service_client", return_value=mock_db), \
             patch("routers.onboarding.apply_template", new_callable=AsyncMock, side_effect=RuntimeError("LLM timeout")):
            response = client.post(
                "/onboarding/setup-wizard",
                json={
                    "industry": "manufacturing",
                    "sub_industry": "",
                    "employee_range": "51-100",
                    "departments": [],
                    "data_import_method": "template",
                    "selected_pipelines": [],
                },
            )

        assert response.status_code == 500

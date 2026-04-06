"""PII検出統合テスト — ingestion / knowledge ルーターへの組み込みを検証。

外部依存（Supabase, LLM, brain モジュール）はすべてモックする。
テスト観点:
- テキスト ingestion 時に電話番号が検出されてマスクされる
- knowledge item 更新時にメールアドレスが検出されてマスクされる
- PII なしのテキストはそのまま保存される
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

# プロジェクトルートを sys.path に追加（conftest.py と同じ）
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


# ---------------------------------------------------------------------------
# ダミーの JWTClaims — 認証ミドルウェアをバイパスするために使用
# ---------------------------------------------------------------------------

FAKE_COMPANY_ID = uuid4()
FAKE_USER_ID = uuid4()


def _make_fake_user() -> MagicMock:
    user = MagicMock()
    user.company_id = FAKE_COMPANY_ID
    user.sub = FAKE_USER_ID
    user.role = "admin"
    user.email = "test@example.com"
    return user


# ---------------------------------------------------------------------------
# ingestion ルーターのテスト
# ---------------------------------------------------------------------------

class TestIngestionPIIDetection:
    """POST /ingestion/text エンドポイントの PII 検出テスト。"""

    def _make_extraction_result(self, text: str) -> MagicMock:
        """extract_knowledge の戻り値モック。"""
        result = MagicMock()
        result.session_id = uuid4()
        result.items = []
        result.model_used = "gemini-2.5-flash"
        result.cost_yen = 0.0
        return result

    def test_phone_number_is_masked_before_extraction(self):
        """電話番号を含むテキストが LLM 抽出前にマスクされる。"""
        from routers.ingestion import router
        from fastapi import FastAPI
        from auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = _make_fake_user

        captured_text: list[str] = []

        async def fake_extract(text: str, company_id, user_id, department=None, category=None):
            captured_text.append(text)
            result = MagicMock()
            result.session_id = uuid4()
            result.items = []
            result.model_used = "gemini-2.5-flash"
            result.cost_yen = 0.0
            return result

        with patch("routers.ingestion.extract_knowledge", side_effect=fake_extract), \
             patch("routers.ingestion.audit_log", new_callable=AsyncMock):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/ingestion/text",
                json={"content": "連絡先は090-1234-5678です"},
            )

        assert response.status_code == 200
        assert len(captured_text) == 1
        # 電話番号がマスクラベルに置換されていること
        assert "090-1234-5678" not in captured_text[0]
        assert "[電話番号]" in captured_text[0]

    def test_no_pii_text_passes_through_unchanged(self):
        """PII を含まないテキストはそのまま抽出処理へ渡される。"""
        from routers.ingestion import router
        from fastapi import FastAPI
        from auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = _make_fake_user

        original_text = "今日の会議の議事録です。特に問題はありません。"
        captured_text: list[str] = []

        async def fake_extract(text: str, company_id, user_id, department=None, category=None):
            captured_text.append(text)
            result = MagicMock()
            result.session_id = uuid4()
            result.items = []
            result.model_used = "gemini-2.5-flash"
            result.cost_yen = 0.0
            return result

        with patch("routers.ingestion.extract_knowledge", side_effect=fake_extract), \
             patch("routers.ingestion.audit_log", new_callable=AsyncMock):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/ingestion/text",
                json={"content": original_text},
            )

        assert response.status_code == 200
        assert len(captured_text) == 1
        assert captured_text[0] == original_text

    def test_pii_detection_error_does_not_block_ingestion(self):
        """PIIDetector が例外を投げても ingestion 処理が続行される。"""
        from routers.ingestion import router
        from fastapi import FastAPI
        from auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = _make_fake_user

        captured_text: list[str] = []
        original_text = "テストテキスト090-1234-5678"

        async def fake_extract(text: str, company_id, user_id, department=None, category=None):
            captured_text.append(text)
            result = MagicMock()
            result.session_id = uuid4()
            result.items = []
            result.model_used = "gemini-2.5-flash"
            result.cost_yen = 0.0
            return result

        with patch("routers.ingestion.extract_knowledge", side_effect=fake_extract), \
             patch("routers.ingestion.audit_log", new_callable=AsyncMock), \
             patch("routers.ingestion.PIIDetector") as mock_detector_cls:
            mock_detector_cls.return_value.detect_and_report.side_effect = RuntimeError("PII engine down")
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/ingestion/text",
                json={"content": original_text},
            )

        # PII 検出が失敗しても 200 で続行
        assert response.status_code == 200
        # 元のテキストがそのまま渡される
        assert len(captured_text) == 1
        assert captured_text[0] == original_text


# ---------------------------------------------------------------------------
# knowledge ルーターのテスト
# ---------------------------------------------------------------------------

class TestKnowledgePIIDetection:
    """PATCH /knowledge/items/{item_id} エンドポイントの PII 検出テスト。"""

    def _make_app(self):
        from routers.knowledge import router
        from fastapi import FastAPI
        from auth.middleware import get_current_user, require_role

        app = FastAPI()
        app.include_router(router)
        # admin ロール要求をバイパス
        app.dependency_overrides[require_role("admin")] = _make_fake_user
        app.dependency_overrides[get_current_user] = _make_fake_user
        return app

    def _make_db_mock(self, item_id: UUID, current_version: int = 1) -> MagicMock:
        """Supabase クライアントのモック。"""
        db = MagicMock()

        # version 取得
        version_result = MagicMock()
        version_result.data = {"version": current_version}

        # update 後の返却データ
        update_result = MagicMock()
        update_result.data = [{
            "id": str(item_id),
            "department": "営業",
            "category": "ルール",
            "item_type": "rule",
            "title": "テストルール",
            "content": "（更新後コンテンツ）",
            "conditions": None,
            "examples": None,
            "exceptions": None,
            "source_type": "manual",
            "confidence": 0.9,
            "version": current_version + 1,
            "is_active": True,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-02T00:00:00",
        }]

        # チェーンメソッドのモック
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.single.return_value = select_chain
        select_chain.execute.return_value = version_result

        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = update_result

        db.table.return_value.select.return_value = select_chain
        db.table.return_value.update.return_value = update_chain

        return db

    def test_email_address_is_masked_on_knowledge_update(self):
        """メールアドレスを含む content が knowledge 更新時にマスクされる。"""
        item_id = uuid4()
        captured_content: list[str] = []

        original_db_mock = self._make_db_mock(item_id)
        # update 呼び出し時のペイロードを捕捉する
        original_update = original_db_mock.table.return_value.update

        def capturing_update(data):
            if "content" in data:
                captured_content.append(data["content"])
            return original_update(data)

        original_db_mock.table.return_value.update = capturing_update

        app = self._make_app()

        with patch("routers.knowledge.get_service_client", return_value=original_db_mock), \
             patch("routers.knowledge.update_item_embedding", new_callable=AsyncMock), \
             patch("routers.knowledge.audit_log", new_callable=AsyncMock):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.patch(
                f"/knowledge/items/{item_id}",
                json={
                    "content": "担当者のメールはtanaka@example.co.jpです",
                    "version": 1,
                },
            )

        assert response.status_code == 200
        assert len(captured_content) == 1
        # メールアドレスがマスクされていること
        assert "tanaka@example.co.jp" not in captured_content[0]
        assert "[メール]" in captured_content[0]

    def test_no_pii_content_passes_through_unchanged(self):
        """PII を含まない content はそのまま保存される。"""
        item_id = uuid4()
        captured_content: list[str] = []
        original_text = "このルールは全社員に適用されます。"

        original_db_mock = self._make_db_mock(item_id)
        original_update = original_db_mock.table.return_value.update

        def capturing_update(data):
            if "content" in data:
                captured_content.append(data["content"])
            return original_update(data)

        original_db_mock.table.return_value.update = capturing_update

        app = self._make_app()

        with patch("routers.knowledge.get_service_client", return_value=original_db_mock), \
             patch("routers.knowledge.update_item_embedding", new_callable=AsyncMock), \
             patch("routers.knowledge.audit_log", new_callable=AsyncMock):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.patch(
                f"/knowledge/items/{item_id}",
                json={
                    "content": original_text,
                    "version": 1,
                },
            )

        assert response.status_code == 200
        assert len(captured_content) == 1
        assert captured_content[0] == original_text

    def test_pii_detection_error_does_not_block_knowledge_update(self):
        """PIIDetector が例外を投げても knowledge 更新が続行される。"""
        item_id = uuid4()
        original_text = "担当者はtanaka@example.co.jpです"

        db_mock = self._make_db_mock(item_id)
        app = self._make_app()

        with patch("routers.knowledge.get_service_client", return_value=db_mock), \
             patch("routers.knowledge.update_item_embedding", new_callable=AsyncMock), \
             patch("routers.knowledge.audit_log", new_callable=AsyncMock), \
             patch("routers.knowledge.PIIDetector") as mock_detector_cls:
            mock_detector_cls.return_value.detect_and_report.side_effect = RuntimeError("PII engine down")
            client = TestClient(app, raise_server_exceptions=True)
            response = client.patch(
                f"/knowledge/items/{item_id}",
                json={
                    "content": original_text,
                    "version": 1,
                },
            )

        # PII 検出失敗でも 200 で続行
        assert response.status_code == 200

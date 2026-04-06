"""LP追跡エンドポイントのテスト。"""
import sys
from types import ModuleType
from unittest.mock import MagicMock

# weasyprint スタブ（routers/webhooks 経由での間接依存を回避）
def _stub_module(name: str) -> MagicMock:
    if name not in sys.modules:
        mock = MagicMock(spec=ModuleType(name))
        sys.modules[name] = mock
    return sys.modules[name]

_stub_module("weasyprint")

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from routers.webhooks import router


app = FastAPI()
app.include_router(router)
client = TestClient(app)


class TestLPEventWebhook:
    """POST /webhooks/lp-event のテスト。"""

    def _mock_db(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return mock_db

    def test_lp_view_cold_short_duration(self):
        """duration_sec < 30 の lp_view は cold。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "lead_id": "lead-123",
                "duration_sec": 10,
            })
        assert resp.status_code == 200
        assert resp.json()["received"] is True
        assert "temperature=cold" in resp.json()["message"]

    def test_lp_view_warm_long_duration(self):
        """duration_sec >= 30 の lp_view は warm。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "lead_id": "lead-123",
                "duration_sec": 30,
            })
        assert resp.status_code == 200
        assert "temperature=warm" in resp.json()["message"]

    def test_cta_click_is_hot(self):
        """cta_click は hot。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db), \
             patch("workers.bpo.sales.chain.trigger_next_pipeline", new_callable=AsyncMock):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "cta_click",
                "lead_id": "lead-456",
                "duration_sec": 0,
            })
        assert resp.status_code == 200
        assert "temperature=hot" in resp.json()["message"]

    def test_doc_download_is_warm(self):
        """doc_download は warm。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "doc_download",
                "lead_id": "lead-789",
                "duration_sec": 0,
            })
        assert resp.status_code == 200
        assert "temperature=warm" in resp.json()["message"]

    def test_schedule_confirmed_is_confirmed(self):
        """schedule_confirmed は confirmed。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "schedule_confirmed",
                "lead_id": "lead-001",
                "duration_sec": 0,
            })
        assert resp.status_code == 200
        assert "temperature=confirmed" in resp.json()["message"]

    def test_no_lead_id_skips_db_insert(self):
        """lead_id がない場合は lead_activities への INSERT をスキップ。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "duration_sec": 5,
            })
        assert resp.status_code == 200
        # lead_id なしなのでinsertが呼ばれていない
        mock_db.table.return_value.insert.assert_not_called()

    def test_with_lead_id_inserts_activity(self):
        """lead_id がある場合は lead_activities に INSERT する。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "lead_id": "lead-999",
                "duration_sec": 5,
            })
        assert resp.status_code == 200
        mock_db.table.assert_called()

    def test_metadata_is_merged_into_activity_data(self):
        """metadata フィールドが activity_data にマージされる。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "lead_id": "lead-001",
                "duration_sec": 5,
                "metadata": {"utm_source": "google", "utm_medium": "cpc"},
            })
        assert resp.status_code == 200

    def test_unknown_event_type_is_cold(self):
        """定義外のイベントタイプは cold。"""
        mock_db = self._mock_db()
        with patch("routers.webhooks.get_service_client", return_value=mock_db):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "unknown_event",
                "duration_sec": 100,
            })
        assert resp.status_code == 200
        assert "temperature=cold" in resp.json()["message"]

    def test_db_error_returns_500(self):
        """DB例外は500を返す。"""
        with patch("routers.webhooks.get_service_client", side_effect=Exception("db down")):
            resp = client.post("/webhooks/lp-event", json={
                "event_type": "lp_view",
                "duration_sec": 5,
            })
        assert resp.status_code == 500

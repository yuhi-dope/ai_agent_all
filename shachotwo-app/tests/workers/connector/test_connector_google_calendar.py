"""GoogleCalendarConnector のユニットテスト。"""
import pytest
from unittest.mock import MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.google_calendar import GoogleCalendarConnector


def _make_connector() -> GoogleCalendarConnector:
    return GoogleCalendarConnector(ConnectorConfig(
        tool_name="google_calendar",
        credentials={"credentials_path": "/tmp/fake.json", "calendar_id": "primary"},
    ))


def _mock_service() -> MagicMock:
    """Calendar API v3 サービスのモック。"""
    svc = MagicMock()
    # events().list()
    svc.events().list().execute.return_value = {
        "items": [
            {"id": "evt1", "summary": "商談 テスト社", "start": {"dateTime": "2026-04-01T10:00:00+09:00"}},
        ]
    }
    # events().get()
    svc.events().get().execute.return_value = {
        "id": "evt1", "summary": "商談 テスト社", "description": ""
    }
    # events().insert()
    svc.events().insert().execute.return_value = {
        "id": "evt_new",
        "summary": "新規商談",
        "conferenceData": {"entryPoints": [{"uri": "https://meet.google.com/abc"}]},
    }
    # events().update()
    svc.events().update().execute.return_value = {
        "id": "evt1", "summary": "更新済み"
    }
    # events().delete()
    svc.events().delete().execute.return_value = None
    # events().watch()
    svc.events().watch().execute.return_value = {
        "id": "ch1", "resourceId": "res1", "expiration": "1711900000000"
    }
    # channels().stop()
    svc.channels().stop().execute.return_value = None
    # calendarList().list()
    svc.calendarList().list().execute.return_value = {"items": []}
    # freebusy().query()
    svc.freebusy().query().execute.return_value = {
        "calendars": {"primary": {"busy": [{"start": "2026-04-01T10:00:00Z", "end": "2026-04-01T11:00:00Z"}]}}
    }
    return svc


class TestGoogleCalendarConnector:
    def setup_method(self) -> None:
        self.connector = _make_connector()
        self.service = _mock_service()

    @pytest.mark.asyncio
    async def test_read_events(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.read_records("events", {})
        assert len(result) == 1
        assert result[0]["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_read_single_event(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.read_records("event", {"event_id": "evt1"})
        assert result[0]["summary"] == "商談 テスト社"

    @pytest.mark.asyncio
    async def test_read_freebusy(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.read_records("freebusy", {})
        assert len(result) == 1
        assert result[0]["calendar_id"] == "primary"

    @pytest.mark.asyncio
    async def test_create_event(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("create_event", {
                "summary": "新規商談",
                "start": "2026-04-01T10:00:00+09:00",
                "end": "2026-04-01T11:00:00+09:00",
            })
        assert result["id"] == "evt_new"

    @pytest.mark.asyncio
    async def test_create_event_with_meet(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("create_event", {
                "summary": "Meet付き商談",
                "start": "2026-04-01T10:00:00+09:00",
                "end": "2026-04-01T11:00:00+09:00",
                "conference": True,
            })
        assert "conferenceData" in result

    @pytest.mark.asyncio
    async def test_update_event(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("update_event", {
                "event_id": "evt1",
                "description": "資料リンク追加",
            })
        assert result["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_delete_event(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("delete_event", {
                "event_id": "evt1",
            })
        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_register_watch(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("watch", {
                "webhook_url": "https://example.com/webhook",
            })
        assert result["channel_id"] == "ch1"
        assert result["resource_id"] == "res1"

    @pytest.mark.asyncio
    async def test_stop_watch(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("stop_watch", {
                "channel_id": "ch1",
                "resource_id": "res1",
            })
        assert result["stopped"] is True

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            assert await self.connector.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        self.service.calendarList().list().execute.side_effect = Exception("auth error")
        with patch.object(self.connector, "_get_service", return_value=self.service):
            assert await self.connector.health_check() is False

    @pytest.mark.asyncio
    async def test_unknown_resource_raises(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            with pytest.raises(ValueError, match="未知のresource"):
                await self.connector.write_record("unknown", {})


class TestGoogleCalendarFactoryRegistration:
    def test_registered_in_factory(self):
        from workers.connector.factory import CONNECTORS
        assert "google_calendar" in CONNECTORS
        assert CONNECTORS["google_calendar"] is GoogleCalendarConnector

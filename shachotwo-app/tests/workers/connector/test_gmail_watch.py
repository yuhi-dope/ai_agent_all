"""Gmail Watch API 管理モジュールのユニットテスト。"""
import pytest
from unittest.mock import MagicMock, patch

from workers.connector import gmail_watch


def _mock_gmail_service() -> MagicMock:
    svc = MagicMock()
    # users().watch()
    svc.users().watch().execute.return_value = {
        "historyId": "12345",
        "expiration": "1711900000000",
    }
    # users().stop()
    svc.users().stop().execute.return_value = None
    # users().history().list()
    svc.users().history().list().execute.return_value = {
        "history": [
            {
                "messagesAdded": [
                    {"message": {"id": "msg1"}},
                    {"message": {"id": "msg2"}},
                ],
            }
        ],
    }
    # users().messages().get()
    def _get_message(**kwargs):
        mock = MagicMock()
        mock.execute.return_value = {
            "id": kwargs.get("id", "msg1"),
            "threadId": "thread1",
            "snippet": "テストメール",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "sender@example.com"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "テスト件名"},
                    {"name": "Date", "value": "2026-03-29"},
                ],
                "body": {"data": ""},
            },
            "labelIds": ["INBOX"],
        }
        return mock

    svc.users().messages().get = _get_message
    return svc


class TestGmailWatch:
    @pytest.mark.asyncio
    async def test_register_watch(self):
        with patch.object(gmail_watch, "_get_gmail_service", return_value=_mock_gmail_service()):
            result = await gmail_watch.register_gmail_watch(
                topic_name="projects/test/topics/gmail-push",
            )
        assert result["historyId"] == "12345"
        assert "expiration" in result

    @pytest.mark.asyncio
    async def test_stop_watch(self):
        with patch.object(gmail_watch, "_get_gmail_service", return_value=_mock_gmail_service()):
            await gmail_watch.stop_gmail_watch()

    @pytest.mark.asyncio
    async def test_process_notification(self):
        with patch.object(gmail_watch, "_get_gmail_service", return_value=_mock_gmail_service()):
            messages = await gmail_watch.process_gmail_notification("10000")
        assert len(messages) == 2
        assert messages[0]["from"] == "sender@example.com"
        assert messages[0]["subject"] == "テスト件名"

    @pytest.mark.asyncio
    async def test_process_notification_empty_history(self):
        svc = _mock_gmail_service()
        svc.users().history().list().execute.return_value = {"history": []}
        with patch.object(gmail_watch, "_get_gmail_service", return_value=svc):
            messages = await gmail_watch.process_gmail_notification("10000")
        assert messages == []

    @pytest.mark.asyncio
    async def test_process_notification_api_error(self):
        svc = _mock_gmail_service()
        svc.users().history().list().execute.side_effect = Exception("API error")
        with patch.object(gmail_watch, "_get_gmail_service", return_value=svc):
            messages = await gmail_watch.process_gmail_notification("10000")
        assert messages == []

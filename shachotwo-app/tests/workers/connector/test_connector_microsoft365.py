"""Microsoft365Connector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.microsoft365 import Microsoft365Connector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_response(
    status_code: int = 200,
    json_data=None,
    content: bytes = b"{}",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = {}
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def _make_error_response(status_code: int, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
    resp.content = b'{"error": "..."}'
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    )
    return resp


def _async_client_ctx(resp: MagicMock):
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _make_connector() -> Microsoft365Connector:
    return Microsoft365Connector(
        ConnectorConfig(
            tool_name="microsoft365",
            credentials={
                "access_token": "ms365-test-token",
                "tenant_id": "tenant-uuid-1234",
            },
        )
    )


# ---------------------------------------------------------------------------
# プロパティ
# ---------------------------------------------------------------------------

class TestMicrosoft365ConnectorProperties:
    def test_base_url(self) -> None:
        connector = _make_connector()
        assert connector.BASE_URL == "https://graph.microsoft.com/v1.0"

    def test_headers_include_bearer_token(self) -> None:
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer ms365-test-token"
        assert connector.headers["Content-Type"] == "application/json"

    def test_tenant_id(self) -> None:
        connector = _make_connector()
        assert connector.tenant_id == "tenant-uuid-1234"


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestMicrosoft365ReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_messages_returns_list(self) -> None:
        messages = [
            {"id": "msg-1", "subject": "会議のご案内", "from": {"emailAddress": {"address": "sender@example.com"}}},
            {"id": "msg-2", "subject": "請求書送付", "from": {"emailAddress": {"address": "billing@example.com"}}},
        ]
        resp = _make_response(200, {"value": messages})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("me/messages")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/me/messages")
        assert result == messages

    @pytest.mark.asyncio
    async def test_read_events_returns_list(self) -> None:
        events = [
            {"id": "evt-1", "subject": "週次ミーティング", "start": {"dateTime": "2024-01-15T10:00:00"}},
        ]
        resp = _make_response(200, {"value": events})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("me/events")

        url = client.get.call_args[0][0]
        assert url.endswith("/me/events")
        assert result == events

    @pytest.mark.asyncio
    async def test_read_joined_teams_returns_list(self) -> None:
        teams = [
            {"id": "team-1", "displayName": "開発チーム"},
            {"id": "team-2", "displayName": "営業チーム"},
        ]
        resp = _make_response(200, {"value": teams})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("me/joinedTeams")

        url = client.get.call_args[0][0]
        assert url.endswith("/me/joinedTeams")
        assert result == teams

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_query_params(self) -> None:
        resp = _make_response(200, {"value": []})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records(
                "me/messages",
                {"$top": 10, "$filter": "isRead eq false"},
            )

        params = client.get.call_args[1]["params"]
        assert params["$top"] == 10
        assert params["$filter"] == "isRead eq false"

    @pytest.mark.asyncio
    async def test_read_records_list_response_returned_directly(self) -> None:
        # value キーなしでリストが直接返るパターン
        data = [{"id": "item-1"}]
        resp = _make_response(200, data)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("me/joinedTeams")

        assert result == data


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestMicrosoft365ReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("me/messages")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("me/messages")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("me/messages")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("me/messages")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestMicrosoft365WriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_sends_email_and_returns_empty_dict_on_202(self) -> None:
        resp = _make_response(202, None, content=b"")
        ctx, client = _async_client_ctx(resp)

        mail_data = {
            "message": {
                "subject": "テスト送信",
                "body": {"contentType": "Text", "content": "テストメールです。"},
                "toRecipients": [{"emailAddress": {"address": "to@example.com"}}],
            }
        }
        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record("me/messages/send", mail_data)

        client.post.assert_called_once()
        url = client.post.call_args[0][0]
        assert url.endswith("/me/messages/send")
        assert result == {}

    @pytest.mark.asyncio
    async def test_write_record_returns_json_on_200(self) -> None:
        created = {"id": "msg-new", "subject": "テスト"}
        resp = _make_response(200, created, content=b'{"id": "msg-new"}')
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "me/messages/send",
                {"message": {"subject": "テスト", "toRecipients": []}},
            )

        assert result == created

    @pytest.mark.asyncio
    async def test_write_record_raises_value_error_for_unknown_resource(self) -> None:
        with pytest.raises(ValueError, match="me/messages/send"):
            await self.connector.write_record(
                "me/events",
                {"subject": "新規イベント"},
            )

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record(
                    "me/messages/send",
                    {"message": {"subject": "テスト", "toRecipients": []}},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record(
                    "me/messages/send",
                    {"message": {"subject": "テスト", "toRecipients": []}},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.write_record(
                    "me/messages/send",
                    {"message": {"subject": "テスト", "toRecipients": []}},
                )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestMicrosoft365HealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, {"id": "user-1", "displayName": "テストユーザー"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/me")

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        resp = _make_response(401, {"error": {"code": "InvalidAuthenticationToken"}})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("ネットワーク障害"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.microsoft365.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestMicrosoft365Factory:
    def test_factory_returns_microsoft365_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {
            "access_token": "ms365-token-xyz",
            "tenant_id": "tenant-uuid-abcd",
        }
        encrypted = encrypt_field(credentials)

        connector = get_connector("microsoft365", encrypted)

        assert isinstance(connector, Microsoft365Connector)
        assert connector.config.tool_name == "microsoft365"
        assert connector.config.credentials == credentials

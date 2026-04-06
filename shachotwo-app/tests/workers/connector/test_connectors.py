"""workers/connector のユニットテスト。httpx.AsyncClient をモックして外部API呼び出しを検証する。"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.kintone import KintoneConnector
from workers.connector.freee import FreeeConnector
from workers.connector.slack import SlackConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """httpx.Response のモックを作成する。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()  # 200 系では何もしない
    return resp


def _make_error_response(status_code: int = 400) -> MagicMock:
    """raise_for_status() で例外を上げるモックを作成する。"""
    import httpx
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
        "error", request=MagicMock(), response=resp
    ))
    return resp


def _async_client_ctx(resp: MagicMock) -> MagicMock:
    """httpx.AsyncClient をコンテキストマネージャとして使えるモックを返す。"""
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.put = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


# ---------------------------------------------------------------------------
# KintoneConnector
# ---------------------------------------------------------------------------

class TestKintoneConnector:
    def setup_method(self) -> None:
        self.config = ConnectorConfig(
            tool_name="kintone",
            credentials={"subdomain": "testco", "api_token": "test-token"},
        )
        self.connector = KintoneConnector(self.config)

    @pytest.mark.asyncio
    async def test_read_records_sends_correct_get_request(self) -> None:
        records = [{"id": {"value": "1"}, "title": {"value": "テスト"}}]
        resp = _make_response(200, {"records": records})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("42")

        client.get.assert_called_once()
        call_kwargs = client.get.call_args
        assert "/records.json" in call_kwargs[0][0]
        assert call_kwargs[1]["params"]["app"] == "42"
        assert result == records

    @pytest.mark.asyncio
    async def test_read_records_with_query_filter(self) -> None:
        resp = _make_response(200, {"records": []})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("42", {"query": "status = \"open\""})

        call_kwargs = client.get.call_args
        assert call_kwargs[1]["params"]["query"] == "status = \"open\""


    @pytest.mark.asyncio
    async def test_read_records_page_passes_limit_param(self) -> None:
        records = [{"$id": {"value": "1"}}]
        resp = _make_response(200, {"records": records})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records_page(
                "99", query="order by $id asc", limit=10
            )

        assert result == records
        call_kwargs = client.get.call_args
        assert call_kwargs[1]["params"]["limit"] == 10
        assert call_kwargs[1]["params"]["query"] == "order by $id asc"
        assert call_kwargs[1]["params"]["app"] == "99"

    @pytest.mark.asyncio
    async def test_write_record_without_id_sends_post(self) -> None:
        resp = _make_response(200, {"id": "100", "revision": "1"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record("42", {"title": {"value": "新規"}})

        client.post.assert_called_once()
        client.put.assert_not_called()
        assert result == {"id": "100", "revision": "1"}

    @pytest.mark.asyncio
    async def test_write_record_with_id_sends_put(self) -> None:
        resp = _make_response(200, {"revision": "2"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "42", {"id": "100", "title": {"value": "更新"}}
            )

        client.put.assert_called_once()
        client.post.assert_not_called()
        # PUT ペイロードに id が含まれること
        put_payload = client.put.call_args[1]["json"]
        assert put_payload["id"] == "100"
        assert result == {"revision": "2"}

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, {})
        ctx, _client = _async_client_ctx(resp)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("network error"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_list_apps_returns_normalized_apps(self) -> None:
        resp = _make_response(
            200,
            {
                "apps": [
                    {"appId": 1, "name": "AppA", "spaceId": "s"},
                    {"id": 2, "name": "AppB"},
                ],
            },
        )
        ctx, client = _async_client_ctx(resp)
        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            out = await self.connector.list_apps()
        assert out == [
            {"appId": "1", "name": "AppA", "spaceId": "s"},
            {"appId": "2", "name": "AppB", "spaceId": None},
        ]
        call_url = client.get.call_args[0][0]
        assert "/apps.json" in call_url

    @pytest.mark.asyncio
    async def test_list_form_fields_flattens_properties(self) -> None:
        resp = _make_response(
            200,
            {
                "properties": {
                    "company": {
                        "code": "company",
                        "label": "会社",
                        "type": "SINGLE_LINE_TEXT",
                        "required": True,
                    },
                },
            },
        )
        ctx, client = _async_client_ctx(resp)
        with patch("workers.connector.kintone.httpx.AsyncClient", return_value=ctx):
            fields = await self.connector.list_form_fields("42")
        assert len(fields) == 1
        assert fields[0]["code"] == "company"
        assert fields[0]["label"] == "会社"
        assert fields[0]["required"] is True
        assert client.get.call_args[1]["params"]["app"] == "42"

    def test_base_url_uses_subdomain(self) -> None:
        assert self.connector.base_url == "https://testco.cybozu.com/k/v1"

    def test_headers_include_api_token(self) -> None:
        assert self.connector.headers["X-Cybozu-API-Token"] == "test-token"


# ---------------------------------------------------------------------------
# FreeeConnector
# ---------------------------------------------------------------------------

class TestFreeeConnector:
    def setup_method(self) -> None:
        self.config = ConnectorConfig(
            tool_name="freee",
            credentials={"access_token": "freee-token-xxx", "company_id": 12345},
        )
        self.connector = FreeeConnector(self.config)

    @pytest.mark.asyncio
    async def test_read_records_calls_deals_endpoint(self) -> None:
        deals = [{"id": 1, "amount": 100000}]
        resp = _make_response(200, {"deals": deals})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.freee.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("deals")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/deals")
        assert result == deals

    @pytest.mark.asyncio
    async def test_read_records_includes_company_id_in_params(self) -> None:
        resp = _make_response(200, {"invoices": []})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.freee.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("invoices")

        params = client.get.call_args[1]["params"]
        assert params["company_id"] == 12345

    @pytest.mark.asyncio
    async def test_write_record_includes_company_id(self) -> None:
        resp = _make_response(200, {"deal": {"id": 99}})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.freee.httpx.AsyncClient", return_value=ctx):
            await self.connector.write_record("deals", {"amount": 50000})

        payload = client.post.call_args[1]["json"]
        assert payload["company_id"] == 12345
        assert payload["amount"] == 50000

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, {"company": {"id": 12345}})
        ctx, _client = _async_client_ctx(resp)

        with patch("workers.connector.freee.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("timeout"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.freee.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# SlackConnector
# ---------------------------------------------------------------------------

class TestSlackConnector:
    def setup_method(self) -> None:
        self.config = ConnectorConfig(
            tool_name="slack",
            credentials={"bot_token": "xoxb-test-token"},
        )
        self.connector = SlackConnector(self.config)

    @pytest.mark.asyncio
    async def test_read_records_channels(self) -> None:
        channels = [{"id": "C001", "name": "general"}]
        resp = _make_response(200, {"channels": channels})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("channels")

        url = client.get.call_args[0][0]
        assert "conversations.list" in url
        assert result == channels

    @pytest.mark.asyncio
    async def test_read_records_messages(self) -> None:
        messages = [{"ts": "1234567890.000001", "text": "hello"}]
        resp = _make_response(200, {"messages": messages})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records(
                "messages", {"channel": "C001", "oldest": "1234567890.000000"}
            )

        url = client.get.call_args[0][0]
        assert "conversations.history" in url
        assert result == messages

    @pytest.mark.asyncio
    async def test_read_records_unknown_resource_returns_empty(self) -> None:
        result = await self.connector.read_records("unknown_resource")
        assert result == []

    @pytest.mark.asyncio
    async def test_write_record_calls_chat_post_message(self) -> None:
        resp = _make_response(200, {"ok": True, "ts": "1234567890.000002"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "message", {"channel": "C001", "text": "テスト送信"}
            )

        url = client.post.call_args[0][0]
        assert "chat.postMessage" in url
        payload = client.post.call_args[1]["json"]
        assert payload["channel"] == "C001"
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_ok(self) -> None:
        resp = _make_response(200, {"ok": True, "team": "TestTeam"})
        ctx, _client = _async_client_ctx(resp)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_not_ok(self) -> None:
        resp = _make_response(200, {"ok": False, "error": "invalid_auth"})
        ctx, _client = _async_client_ctx(resp)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.slack.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_get_connector_returns_kintone_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"subdomain": "testco", "api_token": "token-xxx"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("kintone", encrypted)

        assert isinstance(connector, KintoneConnector)
        assert connector.config.tool_name == "kintone"
        assert connector.config.credentials == credentials

    def test_get_connector_returns_freee_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"access_token": "freee-token", "company_id": 99}
        encrypted = encrypt_field(credentials)

        connector = get_connector("freee", encrypted)

        assert isinstance(connector, FreeeConnector)

    def test_get_connector_returns_slack_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"bot_token": "xoxb-slack-token"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("slack", encrypted)

        assert isinstance(connector, SlackConnector)

    def test_get_connector_raises_for_unknown_tool(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        encrypted = encrypt_field({"token": "xxx"})

        with pytest.raises(ValueError, match="Unknown connector"):
            get_connector("nonexistent_saas", encrypted)

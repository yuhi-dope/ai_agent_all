"""NotionConnector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.notion import NotionConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    return resp


def _make_error_response(status_code: int, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Retry-After": retry_after} if retry_after else {}
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


def _make_connector() -> NotionConnector:
    return NotionConnector(
        ConnectorConfig(
            tool_name="notion",
            credentials={"integration_token": "notion-test-token"},
        )
    )


# ---------------------------------------------------------------------------
# プロパティ
# ---------------------------------------------------------------------------

class TestNotionConnectorProperties:
    def test_base_url(self) -> None:
        connector = _make_connector()
        assert connector.BASE_URL == "https://api.notion.com/v1"

    def test_notion_version(self) -> None:
        connector = _make_connector()
        assert connector.NOTION_VERSION == "2022-06-28"

    def test_headers_include_bearer_token_and_version(self) -> None:
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer notion-test-token"
        assert connector.headers["Notion-Version"] == "2022-06-28"
        assert connector.headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestNotionReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_database_query_returns_results(self) -> None:
        results = [
            {"id": "page-1", "properties": {"名前": {"title": [{"text": {"content": "タスクA"}}]}}},
            {"id": "page-2", "properties": {"名前": {"title": [{"text": {"content": "タスクB"}}]}}},
        ]
        resp = _make_response(200, {"results": results, "next_cursor": None, "has_more": False})
        ctx, client = _async_client_ctx(resp)

        db_id = "db-12345678"
        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records(f"databases/{db_id}/query")

        client.post.assert_called_once()
        url = client.post.call_args[0][0]
        assert url.endswith(f"/databases/{db_id}/query")
        assert result == results

    @pytest.mark.asyncio
    async def test_read_search_returns_results(self) -> None:
        results = [
            {"id": "page-1", "object": "page"},
        ]
        resp = _make_response(200, {"results": results, "next_cursor": None})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records(
                "search", {"query": "プロジェクト計画"}
            )

        url = client.post.call_args[0][0]
        assert url.endswith("/search")
        assert result == results

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_body(self) -> None:
        resp = _make_response(200, {"results": []})
        ctx, client = _async_client_ctx(resp)

        filters = {
            "filter": {"property": "ステータス", "select": {"equals": "完了"}},
            "page_size": 50,
        }
        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("databases/db-1/query", filters)

        body = client.post.call_args[1]["json"]
        assert body["filter"]["property"] == "ステータス"
        assert body["page_size"] == 50

    @pytest.mark.asyncio
    async def test_read_records_empty_results(self) -> None:
        resp = _make_response(200, {"results": [], "next_cursor": None})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("search", {"query": "存在しないページ"})

        assert result == []


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestNotionReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("search")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("databases/db-1/query")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("search")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("search")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestNotionWriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_creates_page(self) -> None:
        created_page = {
            "id": "new-page-id",
            "object": "page",
            "properties": {"タイトル": {"title": [{"text": {"content": "新しいページ"}}]}},
        }
        resp = _make_response(200, created_page)
        ctx, client = _async_client_ctx(resp)

        page_data = {
            "parent": {"database_id": "db-12345678"},
            "properties": {
                "タイトル": {"title": [{"text": {"content": "新しいページ"}}]}
            },
        }
        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record("pages", page_data)

        client.post.assert_called_once()
        url = client.post.call_args[0][0]
        assert url.endswith("/pages")
        assert result == created_page

    @pytest.mark.asyncio
    async def test_write_record_raises_value_error_for_unknown_resource(self) -> None:
        with pytest.raises(ValueError, match="pages"):
            await self.connector.write_record(
                "databases", {"title": [{"text": {"content": "新DB"}}]}
            )

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record(
                    "pages",
                    {"parent": {"database_id": "db-1"}, "properties": {}},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record(
                    "pages",
                    {"parent": {"database_id": "db-1"}, "properties": {}},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.write_record(
                    "pages",
                    {"parent": {"database_id": "db-1"}, "properties": {}},
                )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestNotionHealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, {"id": "user-1", "type": "bot"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/users/me")

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        resp = _make_response(401, {"code": "unauthorized"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("ネットワーク障害"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.notion.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestNotionFactory:
    def test_factory_returns_notion_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"integration_token": "notion-token-xyz"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("notion", encrypted)

        assert isinstance(connector, NotionConnector)
        assert connector.config.tool_name == "notion"
        assert connector.config.credentials == credentials

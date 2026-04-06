"""BacklogConnector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.backlog import BacklogConnector


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


def _make_connector() -> BacklogConnector:
    return BacklogConnector(
        ConnectorConfig(
            tool_name="backlog",
            credentials={
                "api_key": "backlog-test-key",
                "space_key": "myspace",
            },
        )
    )


# ---------------------------------------------------------------------------
# プロパティ
# ---------------------------------------------------------------------------

class TestBacklogConnectorProperties:
    def test_base_url_uses_space_key(self) -> None:
        connector = _make_connector()
        assert connector.base_url == "https://myspace.backlog.com/api/v2"

    def test_api_key(self) -> None:
        connector = _make_connector()
        assert connector.api_key == "backlog-test-key"

    def test_headers_no_authorization(self) -> None:
        connector = _make_connector()
        assert "Authorization" not in connector.headers
        assert connector.headers["Content-Type"] == "application/json"

    def test_with_api_key_prepends_api_key(self) -> None:
        connector = _make_connector()
        result = connector._with_api_key({"count": 10})
        assert result["apiKey"] == "backlog-test-key"
        assert result["count"] == 10


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestBacklogReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_issues_returns_list(self) -> None:
        issues = [
            {"id": 1, "summary": "バグ修正", "status": {"name": "未対応"}},
            {"id": 2, "summary": "機能追加", "status": {"name": "処理中"}},
        ]
        resp = _make_response(200, issues)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("issues")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/issues")
        assert result == issues

    @pytest.mark.asyncio
    async def test_read_projects_returns_list(self) -> None:
        projects = [
            {"id": 10, "projectKey": "PROJ1", "name": "プロジェクト1"},
        ]
        resp = _make_response(200, projects)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("projects")

        url = client.get.call_args[0][0]
        assert url.endswith("/projects")
        assert result == projects

    @pytest.mark.asyncio
    async def test_read_records_sends_api_key_as_query_param(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("issues")

        params = client.get.call_args[1]["params"]
        assert params["apiKey"] == "backlog-test-key"

    @pytest.mark.asyncio
    async def test_read_records_passes_filters(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records(
                "issues",
                {"count": 50, "keyword": "バグ"},
            )

        params = client.get.call_args[1]["params"]
        assert params["count"] == 50
        assert params["keyword"] == "バグ"


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestBacklogReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("issues")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("issues")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("issues")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("issues")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestBacklogWriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_creates_issue(self) -> None:
        created = {"id": 100, "summary": "新規課題", "status": {"name": "未対応"}}
        resp = _make_response(201, created)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "issues",
                {
                    "projectId": 10,
                    "summary": "新規課題",
                    "issueTypeId": 1,
                    "priorityId": 3,
                },
            )

        client.post.assert_called_once()
        url = client.post.call_args[0][0]
        assert url.endswith("/issues")
        assert result == created

    @pytest.mark.asyncio
    async def test_write_record_sends_api_key_as_query_param(self) -> None:
        resp = _make_response(201, {})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            await self.connector.write_record(
                "issues",
                {"projectId": 10, "summary": "テスト", "issueTypeId": 1, "priorityId": 3},
            )

        params = client.post.call_args[1]["params"]
        assert params["apiKey"] == "backlog-test-key"

    @pytest.mark.asyncio
    async def test_write_record_raises_value_error_for_unknown_resource(self) -> None:
        with pytest.raises(ValueError, match="issues"):
            await self.connector.write_record("projects", {"name": "新プロジェクト"})

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record(
                    "issues",
                    {"projectId": 10, "summary": "テスト", "issueTypeId": 1, "priorityId": 3},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record(
                    "issues",
                    {"projectId": 10, "summary": "テスト", "issueTypeId": 1, "priorityId": 3},
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.write_record(
                    "issues",
                    {"projectId": 10, "summary": "テスト", "issueTypeId": 1, "priorityId": 3},
                )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestBacklogHealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/projects")

    @pytest.mark.asyncio
    async def test_health_check_sends_api_key(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            await self.connector.health_check()

        params = client.get.call_args[1]["params"]
        assert params["apiKey"] == "backlog-test-key"

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        resp = _make_response(401, {"error": "unauthorized"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("ネットワーク障害"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.backlog.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestBacklogFactory:
    def test_factory_returns_backlog_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"api_key": "backlog-key-xyz", "space_key": "myspace"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("backlog", encrypted)

        assert isinstance(connector, BacklogConnector)
        assert connector.config.tool_name == "backlog"
        assert connector.config.credentials == credentials

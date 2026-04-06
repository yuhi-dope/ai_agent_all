"""JobcanConnector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.jobcan import JobcanConnector


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
    client.put = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _make_connector() -> JobcanConnector:
    return JobcanConnector(
        ConnectorConfig(
            tool_name="jobcan",
            credentials={
                "access_token": "jobcan-test-token",
                "company_id": "COMP001",
            },
        )
    )


# ---------------------------------------------------------------------------
# プロパティ
# ---------------------------------------------------------------------------

class TestJobcanConnectorProperties:
    def test_base_url(self) -> None:
        connector = _make_connector()
        assert connector.BASE_URL == "https://ssl.jobcan.jp/api/staff"

    def test_headers_include_bearer_token(self) -> None:
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer jobcan-test-token"
        assert connector.headers["Content-Type"] == "application/json"

    def test_company_id(self) -> None:
        connector = _make_connector()
        assert connector.company_id == "COMP001"


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestJobcanReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_attendance_records_returns_list(self) -> None:
        records = [
            {"employee_id": "EMP001", "date": "2024-01-15", "clock_in": "09:00"},
            {"employee_id": "EMP002", "date": "2024-01-15", "clock_in": "08:55"},
        ]
        resp = _make_response(200, records)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("attendance_records")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/attendance_records")
        assert result == records

    @pytest.mark.asyncio
    async def test_read_employees_returns_list(self) -> None:
        employees = [
            {"employee_id": "EMP001", "name": "田中 一郎"},
            {"employee_id": "EMP002", "name": "鈴木 花子"},
        ]
        resp = _make_response(200, employees)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("employees")

        url = client.get.call_args[0][0]
        assert url.endswith("/employees")
        assert result == employees

    @pytest.mark.asyncio
    async def test_read_records_sends_company_id_param(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("attendance_records")

        params = client.get.call_args[1]["params"]
        assert params["company_id"] == "COMP001"

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_params(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records(
                "attendance_records",
                {"start_date": "2024-01-01", "end_date": "2024-01-31"},
            )

        params = client.get.call_args[1]["params"]
        assert params["start_date"] == "2024-01-01"
        assert params["end_date"] == "2024-01-31"

    @pytest.mark.asyncio
    async def test_read_records_dict_response_with_resource_key(self) -> None:
        resp = _make_response(
            200,
            {"attendance_records": [{"employee_id": "EMP001"}], "total": 1},
        )
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("attendance_records")

        assert result == [{"employee_id": "EMP001"}]


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestJobcanReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("attendance_records")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("attendance_records")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("attendance_records")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("attendance_records")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestJobcanWriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_without_id_sends_post(self) -> None:
        created = {"id": "att-100", "employee_id": "EMP001", "date": "2024-01-15"}
        resp = _make_response(201, created)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "attendance_records",
                {"employee_id": "EMP001", "date": "2024-01-15", "clock_in": "09:05"},
            )

        client.post.assert_called_once()
        client.put.assert_not_called()
        url = client.post.call_args[0][0]
        assert url.endswith("/attendance_records")
        assert result == created

    @pytest.mark.asyncio
    async def test_write_record_with_id_sends_put(self) -> None:
        updated = {"id": "att-1", "employee_id": "EMP001", "clock_in": "09:00"}
        resp = _make_response(200, updated)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "attendance_records",
                {"id": "att-1", "clock_in": "09:00"},
            )

        client.put.assert_called_once()
        client.post.assert_not_called()
        url = client.put.call_args[0][0]
        assert url.endswith("/attendance_records/att-1")
        assert result == updated

    @pytest.mark.asyncio
    async def test_write_record_includes_company_id_in_payload(self) -> None:
        resp = _make_response(201, {})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            await self.connector.write_record(
                "attendance_records",
                {"employee_id": "EMP001"},
            )

        payload = client.post.call_args[1]["json"]
        assert payload["company_id"] == "COMP001"

    @pytest.mark.asyncio
    async def test_write_record_raises_value_error_for_unknown_resource(self) -> None:
        with pytest.raises(ValueError, match="attendance_records"):
            await self.connector.write_record("employees", {"name": "テスト"})

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record(
                    "attendance_records", {"employee_id": "EMP001"}
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record(
                    "attendance_records", {"employee_id": "EMP001"}
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.write_record(
                    "attendance_records", {"employee_id": "EMP001"}
                )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestJobcanHealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/attendance_records")

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        resp = _make_response(401, {"error": "unauthorized"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "internal server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("ネットワーク障害"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.jobcan.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestJobcanFactory:
    def test_factory_returns_jobcan_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"access_token": "tok-xyz", "company_id": "COMP001"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("jobcan", encrypted)

        assert isinstance(connector, JobcanConnector)
        assert connector.config.tool_name == "jobcan"
        assert connector.config.credentials == credentials

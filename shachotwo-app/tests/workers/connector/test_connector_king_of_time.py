"""KingOfTimeConnector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.king_of_time import KingOfTimeConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None) -> MagicMock:
    """httpx.Response のモックを作成する。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    return resp


def _make_error_response(status_code: int, retry_after: str | None = None) -> MagicMock:
    """エラーレスポンスのモックを作成する。"""
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
    """httpx.AsyncClient をコンテキストマネージャとして使えるモックを返す。"""
    client = AsyncMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    client.put = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _make_connector() -> KingOfTimeConnector:
    return KingOfTimeConnector(
        ConnectorConfig(
            tool_name="king_of_time",
            credentials={"access_token": "kot-test-token"},
        )
    )


# ---------------------------------------------------------------------------
# headers / BASE_URL
# ---------------------------------------------------------------------------

class TestKingOfTimeConnectorProperties:
    def test_base_url(self) -> None:
        connector = _make_connector()
        assert connector.BASE_URL == "https://api.kingtime.jp/v1"

    def test_headers_include_bearer_token(self) -> None:
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer kot-test-token"
        assert connector.headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestKingOfTimeReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_daily_workings_returns_list(self) -> None:
        records = [
            {"employee_key": "EMP001", "date": "2024-01-15", "work_minutes": 480},
            {"employee_key": "EMP002", "date": "2024-01-15", "work_minutes": 450},
        ]
        resp = _make_response(200, records)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("daily_workings")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/daily_workings")
        assert result == records

    @pytest.mark.asyncio
    async def test_read_monthly_workings_with_date_filter(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records(
                "monthly_workings",
                {"start_date": "2024-01-01", "end_date": "2024-01-31"},
            )

        params = client.get.call_args[1]["params"]
        assert params["start_date"] == "2024-01-01"
        assert params["end_date"] == "2024-01-31"

    @pytest.mark.asyncio
    async def test_read_employees_returns_list(self) -> None:
        employees = [
            {"employee_key": "EMP001", "last_name": "田中", "first_name": "一郎"},
        ]
        resp = _make_response(200, employees)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("employees")

        url = client.get.call_args[0][0]
        assert url.endswith("/employees")
        assert result == employees

    @pytest.mark.asyncio
    async def test_read_timerecords_with_employee_key_filter(self) -> None:
        timerecords = [
            {"id": "tr-1", "employee_key": "EMP001", "datetime": "2024-01-15T09:00:00"}
        ]
        resp = _make_response(200, timerecords)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records(
                "timerecords", {"employee_key": "EMP001", "date": "2024-01-15"}
            )

        params = client.get.call_args[1]["params"]
        assert params["employee_key"] == "EMP001"
        assert params["date"] == "2024-01-15"
        assert result == timerecords

    @pytest.mark.asyncio
    async def test_read_divisions_returns_list(self) -> None:
        divisions = [{"division_code": "DIV01", "name": "開発部"}]
        resp = _make_response(200, divisions)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("divisions")

        url = client.get.call_args[0][0]
        assert url.endswith("/divisions")
        assert result == divisions

    @pytest.mark.asyncio
    async def test_read_records_dict_response_with_resource_key(self) -> None:
        # API がリソース名キーの辞書で返すパターン
        resp = _make_response(
            200,
            {"employees": [{"employee_key": "EMP001"}], "total": 1},
        )
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("employees")

        assert result == [{"employee_key": "EMP001"}]


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestKingOfTimeReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("daily_workings")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("daily_workings")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("daily_workings")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("daily_workings")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestKingOfTimeWriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_without_id_sends_post(self) -> None:
        new_record = {"id": "tr-100", "employee_key": "EMP001"}
        resp = _make_response(201, new_record)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "timerecords",
                {
                    "employee_key": "EMP001",
                    "datetime": "2024-01-15T09:05:00",
                    "type": "clock_in",
                },
            )

        client.post.assert_called_once()
        client.put.assert_not_called()
        url = client.post.call_args[0][0]
        assert url.endswith("/timerecords")
        assert result == new_record

    @pytest.mark.asyncio
    async def test_write_record_with_id_sends_put(self) -> None:
        updated_record = {"id": "tr-1", "employee_key": "EMP001", "datetime": "2024-01-15T09:00:00"}
        resp = _make_response(200, updated_record)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "timerecords",
                {"id": "tr-1", "datetime": "2024-01-15T09:00:00"},
            )

        client.put.assert_called_once()
        client.post.assert_not_called()
        url = client.put.call_args[0][0]
        assert url.endswith("/timerecords/tr-1")
        assert result == updated_record

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record(
                    "timerecords", {"employee_key": "EMP001"}
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record(
                    "timerecords", {"employee_key": "EMP001"}
                )

    @pytest.mark.asyncio
    async def test_write_record_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.write_record(
                    "timerecords", {"employee_key": "EMP001"}
                )


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestKingOfTimeHealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/employees")

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        # 401 は認証エラーだが接続自体は成功（status_code < 500 なので True）
        resp = _make_response(401, {"error": "unauthorized"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "internal server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_network_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("ネットワーク障害"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.king_of_time.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# map_to_attendance_data
# ---------------------------------------------------------------------------

class TestKingOfTimeMapToAttendanceData:
    def test_maps_all_fields_correctly(self) -> None:
        raw = {
            "employee_key": "EMP001",
            "date": "2024-01-15",
            "start_time": "09:00:00",
            "end_time": "18:30:00",
            "break_minutes": 60,
            "work_minutes": 480,
            "overtime_minutes": 30,
            "late": False,
            "early_leave": False,
            "absence": False,
        }

        result = KingOfTimeConnector.map_to_attendance_data(raw)

        assert result["employee_id"] == "EMP001"
        assert result["date"] == "2024-01-15"
        assert result["clock_in"] == "09:00"
        assert result["clock_out"] == "18:30"
        assert result["break_minutes"] == 60
        assert result["working_minutes"] == 480
        assert result["overtime_minutes"] == 30
        assert result["late"] is False
        assert result["early_leave"] is False
        assert result["absence"] is False

    def test_maps_clock_in_out_truncates_seconds(self) -> None:
        raw = {
            "employee_key": "EMP002",
            "date": "2024-01-16",
            "start_time": "08:55:23",
            "end_time": "17:45:12",
            "work_minutes": 410,
        }

        result = KingOfTimeConnector.map_to_attendance_data(raw)

        assert result["clock_in"] == "08:55"
        assert result["clock_out"] == "17:45"

    def test_maps_absence_day(self) -> None:
        raw = {
            "employee_key": "EMP003",
            "date": "2024-01-17",
            "start_time": "",
            "end_time": "",
            "work_minutes": 0,
            "absence": True,
        }

        result = KingOfTimeConnector.map_to_attendance_data(raw)

        assert result["clock_in"] == ""
        assert result["clock_out"] == ""
        assert result["working_minutes"] == 0
        assert result["absence"] is True

    def test_maps_late_arrival(self) -> None:
        raw = {
            "employee_key": "EMP004",
            "date": "2024-01-18",
            "start_time": "10:15:00",
            "end_time": "19:00:00",
            "work_minutes": 465,
            "late": True,
            "early_leave": False,
        }

        result = KingOfTimeConnector.map_to_attendance_data(raw)

        assert result["late"] is True
        assert result["clock_in"] == "10:15"

    def test_maps_missing_fields_as_defaults(self) -> None:
        result = KingOfTimeConnector.map_to_attendance_data({})

        assert result["employee_id"] == ""
        assert result["date"] == ""
        assert result["clock_in"] == ""
        assert result["clock_out"] == ""
        assert result["break_minutes"] == 0
        assert result["working_minutes"] == 0
        assert result["overtime_minutes"] == 0
        assert result["late"] is False
        assert result["early_leave"] is False
        assert result["absence"] is False

    def test_maps_clock_in_out_from_alternative_field_names(self) -> None:
        # clock_in / clock_out フィールド名でも動作すること
        raw = {
            "employee_key": "EMP005",
            "date": "2024-01-19",
            "clock_in": "09:05",
            "clock_out": "18:05",
            "work_minutes": 480,
        }

        result = KingOfTimeConnector.map_to_attendance_data(raw)

        assert result["clock_in"] == "09:05"
        assert result["clock_out"] == "18:05"


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestKingOfTimeFactory:
    def test_factory_returns_king_of_time_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"access_token": "kot-token-xyz"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("king_of_time", encrypted)

        assert isinstance(connector, KingOfTimeConnector)
        assert connector.config.tool_name == "king_of_time"
        assert connector.config.credentials == credentials

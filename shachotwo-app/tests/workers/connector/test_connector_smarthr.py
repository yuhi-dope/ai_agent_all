"""SmartHRConnector のユニットテスト。外部APIは全てモック。"""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from workers.connector.base import ConnectorConfig
from workers.connector.smarthr import SmartHRConnector


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
    client.patch = AsyncMock(return_value=resp)
    client.put = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _make_connector(subdomain: str | None = None) -> SmartHRConnector:
    credentials: dict = {"access_token": "smarthr-test-token"}
    if subdomain:
        credentials["subdomain"] = subdomain
    return SmartHRConnector(
        ConnectorConfig(tool_name="smarthr", credentials=credentials)
    )


# ---------------------------------------------------------------------------
# base_url / headers
# ---------------------------------------------------------------------------

class TestSmartHRConnectorProperties:
    def test_base_url_default(self) -> None:
        connector = _make_connector()
        assert connector.base_url == "https://api.smarthr.jp/api/v1"

    def test_base_url_with_subdomain(self) -> None:
        connector = _make_connector(subdomain="testco")
        assert connector.base_url == "https://testco.smarthr.jp/api/v1"

    def test_headers_include_bearer_token(self) -> None:
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer smarthr-test-token"
        assert connector.headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# read_records — 正常系
# ---------------------------------------------------------------------------

class TestSmartHRReadRecords:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_employees_returns_list(self) -> None:
        employees = [
            {"id": "emp-1", "emp_code": "E001", "last_name": "山田", "first_name": "太郎"},
            {"id": "emp-2", "emp_code": "E002", "last_name": "鈴木", "first_name": "花子"},
        ]
        resp = _make_response(200, employees)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("employees")

        client.get.assert_called_once()
        url = client.get.call_args[0][0]
        assert url.endswith("/employees")
        assert result == employees

    @pytest.mark.asyncio
    async def test_read_employee_detail_wraps_in_list(self) -> None:
        employee = {"id": "emp-1", "emp_code": "E001", "last_name": "山田"}
        resp = _make_response(200, employee)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("employees/emp-1")

        assert result == [employee]

    @pytest.mark.asyncio
    async def test_read_departments_passes_correct_url(self) -> None:
        departments = [{"id": "dept-1", "name": "営業部"}]
        resp = _make_response(200, departments)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("departments")

        url = client.get.call_args[0][0]
        assert url.endswith("/departments")
        assert result == departments

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_params(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            await self.connector.read_records("employees", {"page": 2, "per_page": 50})

        params = client.get.call_args[1]["params"]
        assert params["page"] == 2
        assert params["per_page"] == 50

    @pytest.mark.asyncio
    async def test_read_dependents_passes_employee_id_in_url(self) -> None:
        dependents = [{"id": "dep-1", "last_name": "山田", "first_name": "次郎"}]
        resp = _make_response(200, dependents)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.read_records("dependents/emp-1")

        url = client.get.call_args[0][0]
        assert "dependents/emp-1" in url
        assert result == dependents


# ---------------------------------------------------------------------------
# read_records — 異常系
# ---------------------------------------------------------------------------

class TestSmartHRReadRecordsErrors:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("employees")

    @pytest.mark.asyncio
    async def test_read_records_raises_permission_error_on_403(self) -> None:
        resp = _make_error_response(403)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.read_records("employees")

    @pytest.mark.asyncio
    async def test_read_records_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="60")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.read_records("employees")

    @pytest.mark.asyncio
    async def test_read_records_raises_timeout_exception(self) -> None:
        ctx = MagicMock()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.TimeoutException):
                await self.connector.read_records("employees")


# ---------------------------------------------------------------------------
# write_record — 正常系
# ---------------------------------------------------------------------------

class TestSmartHRWriteRecord:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_write_record_without_id_sends_post(self) -> None:
        new_emp = {"id": "emp-100", "emp_code": "E100", "last_name": "新規"}
        resp = _make_response(201, new_emp)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "employees", {"last_name": "新規", "first_name": "社員"}
            )

        client.post.assert_called_once()
        client.patch.assert_not_called()
        url = client.post.call_args[0][0]
        assert url.endswith("/employees")
        assert result == new_emp

    @pytest.mark.asyncio
    async def test_write_record_with_id_sends_patch(self) -> None:
        updated_emp = {"id": "emp-1", "last_name": "山田", "email": "yamada@example.com"}
        resp = _make_response(200, updated_emp)
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            result = await self.connector.write_record(
                "employees", {"id": "emp-1", "email": "yamada@example.com"}
            )

        client.patch.assert_called_once()
        client.post.assert_not_called()
        url = client.patch.call_args[0][0]
        assert url.endswith("/employees/emp-1")
        assert result == updated_emp

    @pytest.mark.asyncio
    async def test_write_record_dependent_sends_post_to_correct_url(self) -> None:
        resp = _make_response(201, {"id": "dep-1"})
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            await self.connector.write_record(
                "dependents/emp-1",
                {"last_name": "山田", "first_name": "次郎", "relation": "child"},
            )

        url = client.post.call_args[0][0]
        assert "dependents/emp-1" in url

    @pytest.mark.asyncio
    async def test_write_record_raises_permission_error_on_401(self) -> None:
        resp = _make_error_response(401)
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(PermissionError, match="認証エラー"):
                await self.connector.write_record("employees", {"last_name": "テスト"})

    @pytest.mark.asyncio
    async def test_write_record_raises_runtime_error_on_429(self) -> None:
        resp = _make_error_response(429, retry_after="30")
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(RuntimeError, match="レート制限"):
                await self.connector.write_record("employees", {"last_name": "テスト"})


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------

class TestSmartHRHealthCheck:
    def setup_method(self) -> None:
        self.connector = _make_connector()

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_200(self) -> None:
        resp = _make_response(200, [])
        ctx, client = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True
        url = client.get.call_args[0][0]
        assert url.endswith("/employees")

    @pytest.mark.asyncio
    async def test_health_check_returns_true_for_401(self) -> None:
        # 401 は認証エラーだが接続自体は成功（status_code < 500 なので True）
        resp = _make_response(401, {"error": "unauthorized"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_for_500(self) -> None:
        resp = _make_response(500, {"error": "internal server error"})
        ctx, _ = _async_client_ctx(resp)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self) -> None:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("接続拒否"))
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("workers.connector.smarthr.httpx.AsyncClient", return_value=ctx):
            ok = await self.connector.health_check()

        assert ok is False


# ---------------------------------------------------------------------------
# map_to_employee_data
# ---------------------------------------------------------------------------

class TestSmartHRMapToEmployeeData:
    def test_maps_all_fields_correctly(self) -> None:
        raw = {
            "id": "emp-1",
            "emp_code": "E001",
            "last_name": "山田",
            "first_name": "太郎",
            "last_name_kana": "ヤマダ",
            "first_name_kana": "タロウ",
            "email": "yamada@example.com",
            "department": {"id": "dept-1", "name": "営業部"},
            "employment_type": {"id": "et-1", "name": "正社員"},
            "employment_status": "employed",
            "entered_at": "2020-04-01",
            "resigned_at": None,
            "gender": "male",
            "birth_date": "1990-01-15",
        }

        result = SmartHRConnector.map_to_employee_data(raw)

        assert result["employee_id"] == "E001"
        assert result["name"] == "山田 太郎"
        assert result["name_kana"] == "ヤマダ タロウ"
        assert result["email"] == "yamada@example.com"
        assert result["department"] == "営業部"
        assert result["employment_type"] == "正社員"
        assert result["employment_status"] == "在籍"
        assert result["joined_at"] == "2020-04-01"
        assert result["resigned_at"] is None
        assert result["gender"] == "男性"
        assert result["birth_date"] == "1990-01-15"

    def test_maps_resigned_employee(self) -> None:
        raw = {
            "emp_code": "E999",
            "last_name": "退職",
            "first_name": "者",
            "employment_status": "retired",
            "resigned_at": "2023-03-31",
        }

        result = SmartHRConnector.map_to_employee_data(raw)

        assert result["employment_status"] == "退職"
        assert result["resigned_at"] == "2023-03-31"

    def test_maps_unknown_gender_as_raw_value(self) -> None:
        raw = {"emp_code": "E002", "gender": "other_value"}

        result = SmartHRConnector.map_to_employee_data(raw)

        assert result["gender"] == "other_value"

    def test_maps_missing_fields_as_empty_string(self) -> None:
        result = SmartHRConnector.map_to_employee_data({})

        assert result["employee_id"] == ""
        assert result["name"] == ""
        assert result["email"] == ""
        assert result["department"] == ""

    def test_maps_department_as_none_gracefully(self) -> None:
        raw = {"emp_code": "E003", "department": None}

        result = SmartHRConnector.map_to_employee_data(raw)

        assert result["department"] == ""


# ---------------------------------------------------------------------------
# Factory 登録確認
# ---------------------------------------------------------------------------

class TestSmartHRFactory:
    def test_factory_returns_smarthr_connector(self) -> None:
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {"access_token": "smarthr-token-xyz"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("smarthr", encrypted)

        assert isinstance(connector, SmartHRConnector)
        assert connector.config.tool_name == "smarthr"
        assert connector.config.credentials == credentials

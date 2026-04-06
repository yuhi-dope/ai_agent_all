"""YayoiConnector のユニットテスト。外部APIは全てモック。"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from workers.connector.base import ConnectorConfig
from workers.connector.yayoi import YayoiConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_config(extra: dict | None = None) -> ConnectorConfig:
    creds: dict = {
        "access_token": "test_yayoi_token",
        "company_id": "company_001",
    }
    if extra:
        creds.update(extra)
    return ConnectorConfig(tool_name="yayoi", credentials=creds)


def _make_connector(extra_creds: dict | None = None) -> YayoiConnector:
    return YayoiConnector(_make_config(extra_creds))


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    return resp


# ---------------------------------------------------------------------------
# 正常系: read_records
# ---------------------------------------------------------------------------


class TestReadRecords:
    @pytest.mark.asyncio
    async def test_read_sales_returns_list(self) -> None:
        """sales リソースが売上リストを返すこと。"""
        connector = _make_connector()
        fake_sales = [
            {"id": "sale_001", "description": "製品A売上", "amount": 500000},
            {"id": "sale_002", "description": "製品B売上", "amount": 300000},
        ]
        mock_resp = _mock_response({"sales": fake_sales})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("sales")

        assert result == fake_sales
        async_client.get.assert_called_once()
        call_args = async_client.get.call_args
        assert "sales" in call_args[0][0]
        assert call_args[1]["params"]["company_id"] == "company_001"

    @pytest.mark.asyncio
    async def test_read_expenses_returns_list(self) -> None:
        """expenses リソースが経費リストを返すこと。"""
        connector = _make_connector()
        fake_expenses = [
            {"id": "exp_001", "description": "消耗品費", "amount": 3000},
        ]
        mock_resp = _mock_response({"expenses": fake_expenses})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("expenses")

        assert result == fake_expenses

    @pytest.mark.asyncio
    async def test_read_journal_entries_returns_list(self) -> None:
        """journal_entries リソースが仕訳リストを返すこと。"""
        connector = _make_connector()
        fake_entries = [
            {"id": "je_001", "description": "売上高", "debit_amount": 100000, "credit_amount": 100000},
        ]
        mock_resp = _mock_response({"journal_entries": fake_entries})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("journal_entries")

        assert result == fake_entries

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_params(self) -> None:
        """filters がクエリパラメータとして渡されること。"""
        connector = _make_connector()
        mock_resp = _mock_response({"sales": []})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            await connector.read_records("sales", filters={"fiscal_year": 2025, "month": 4})

        call_params = async_client.get.call_args[1]["params"]
        assert call_params["fiscal_year"] == 2025
        assert call_params["month"] == 4
        assert call_params["company_id"] == "company_001"

    @pytest.mark.asyncio
    async def test_read_records_falls_back_to_data_key(self) -> None:
        """レスポンスにリソース名キーがない場合は data キーで取得すること。"""
        connector = _make_connector()
        fake_data = [{"id": "sale_003"}]
        mock_resp = _mock_response({"data": fake_data})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("sales")

        assert result == fake_data

    @pytest.mark.asyncio
    async def test_read_records_raises_for_unknown_resource(self) -> None:
        """未対応リソースを指定した場合に ValueError が発生すること。"""
        connector = _make_connector()

        with pytest.raises(ValueError, match="Unknown resource"):
            await connector.read_records("unknown_resource")

    @pytest.mark.asyncio
    async def test_read_records_raises_on_auth_error(self) -> None:
        """認証エラー（401）時に HTTPStatusError が伝播すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({}, status_code=401)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.read_records("sales")

    @pytest.mark.asyncio
    async def test_read_records_raises_on_rate_limit(self) -> None:
        """レート制限（429）時に HTTPStatusError が伝播すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({}, status_code=429)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.read_records("sales")

    @pytest.mark.asyncio
    async def test_read_records_raises_on_connection_timeout(self) -> None:
        """接続タイムアウト時に ConnectTimeout が伝播すること。"""
        connector = _make_connector()

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.ConnectTimeout):
                await connector.read_records("expenses")


# ---------------------------------------------------------------------------
# 正常系: write_record
# ---------------------------------------------------------------------------


class TestWriteRecord:
    @pytest.mark.asyncio
    async def test_write_journal_entry_returns_api_response(self) -> None:
        """journal_entries への write が作成された仕訳を返すこと。"""
        connector = _make_connector()
        created_entry = {
            "id": "je_new_001",
            "description": "売上高",
            "debit_amount": 200000,
            "credit_amount": 200000,
        }
        mock_resp = _mock_response(created_entry)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.write_record(
                "journal_entries",
                {"description": "売上高", "debit_amount": 200000},
            )

        assert result == created_entry
        async_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_journal_entry_includes_company_id(self) -> None:
        """write_record が company_id をペイロードに自動付与すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({"id": "je_001"})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            await connector.write_record(
                "journal_entries",
                {"description": "仕訳テスト"},
            )

        call_json = async_client.post.call_args[1]["json"]
        assert call_json["company_id"] == "company_001"
        assert call_json["description"] == "仕訳テスト"

    @pytest.mark.asyncio
    async def test_write_non_writable_resource_raises(self) -> None:
        """write 非対応リソース（sales）を指定した場合に ValueError が発生すること。"""
        connector = _make_connector()

        with pytest.raises(ValueError, match="write_record"):
            await connector.write_record("sales", {"description": "テスト売上"})

    @pytest.mark.asyncio
    async def test_write_expenses_raises(self) -> None:
        """expenses は write 非対応のため ValueError が発生すること。"""
        connector = _make_connector()

        with pytest.raises(ValueError, match="write_record"):
            await connector.write_record("expenses", {"description": "経費"})

    @pytest.mark.asyncio
    async def test_write_journal_entry_raises_on_auth_error(self) -> None:
        """認証エラー（401）時に HTTPStatusError が伝播すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({}, status_code=401)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.write_record("journal_entries", {"description": "テスト"})

    @pytest.mark.asyncio
    async def test_write_journal_entry_uses_correct_endpoint(self) -> None:
        """write_record が正しいエンドポイントを叩くこと。"""
        connector = _make_connector()
        mock_resp = _mock_response({"id": "je_001"})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            await connector.write_record("journal_entries", {"description": "テスト"})

        call_url = async_client.post.call_args[0][0]
        assert "yayoi-kaikei.jp/api/v1/journal_entries" in call_url


# ---------------------------------------------------------------------------
# 正常系: health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self) -> None:
        """事業所エンドポイントが 200 を返すと True になること。"""
        connector = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_auth_error(self) -> None:
        """認証エラー（401）時に False を返すこと。"""
        connector = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_connection_timeout(self) -> None:
        """接続タイムアウト時に False を返すこと。"""
        connector = _make_connector()

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            result = await connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_calls_companies_endpoint(self) -> None:
        """health_check が companies エンドポイントを叩くこと。"""
        connector = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.yayoi.httpx.AsyncClient", return_value=async_client):
            await connector.health_check()

        call_url = async_client.get.call_args[0][0]
        assert "yayoi-kaikei.jp/api/v1/companies/company_001" in call_url


# ---------------------------------------------------------------------------
# headers プロパティ
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_headers_include_bearer_token(self) -> None:
        """Authorization ヘッダーが Bearer トークン形式であること。"""
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer test_yayoi_token"

    def test_headers_include_content_type(self) -> None:
        """Content-Type が application/json であること。"""
        connector = _make_connector()
        assert connector.headers["Content-Type"] == "application/json"

    def test_company_id_property_returns_correct_value(self) -> None:
        """company_id プロパティが credentials から正しく取れること。"""
        connector = _make_connector()
        assert connector.company_id == "company_001"


# ---------------------------------------------------------------------------
# Factory 登録テスト
# ---------------------------------------------------------------------------


class TestFactoryRegistration:
    def test_yayoi_key_is_in_connectors_dict(self) -> None:
        """CONNECTORS 辞書に 'yayoi' キーが登録されていること。"""
        from workers.connector.factory import CONNECTORS

        assert "yayoi" in CONNECTORS
        assert CONNECTORS["yayoi"] is YayoiConnector

    def test_get_connector_returns_yayoi_connector(self) -> None:
        """factory.get_connector('yayoi') が YayoiConnector を返すこと。"""
        from security.encryption import encrypt_field
        from workers.connector.factory import get_connector

        credentials = {"access_token": "tok_yayoi_xyz", "company_id": "company_abc"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("yayoi", encrypted)

        assert isinstance(connector, YayoiConnector)
        assert connector.config.tool_name == "yayoi"
        assert connector.config.credentials["company_id"] == "company_abc"

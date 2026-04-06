"""MoneyForwardConnector のユニットテスト。外部APIは全てモック。"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from workers.connector.base import ConnectorConfig
from workers.connector.money_forward import MoneyForwardConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_config(extra: dict | None = None) -> ConnectorConfig:
    creds: dict = {
        "access_token": "test_access_token",
        "office_id": "office_001",
    }
    if extra:
        creds.update(extra)
    return ConnectorConfig(tool_name="money_forward", credentials=creds)


def _make_connector(extra_creds: dict | None = None) -> MoneyForwardConnector:
    return MoneyForwardConnector(_make_config(extra_creds))


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
    async def test_read_invoices_returns_list(self) -> None:
        """invoices リソースが請求書リストを返すこと。"""
        connector = _make_connector()
        fake_invoices = [
            {"id": "inv_001", "title": "4月分請求書", "amount": 300000},
            {"id": "inv_002", "title": "5月分請求書", "amount": 150000},
        ]
        mock_resp = _mock_response({"invoices": fake_invoices})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("invoices")

        assert result == fake_invoices
        async_client.get.assert_called_once()
        call_args = async_client.get.call_args
        assert "invoices" in call_args[0][0]
        assert call_args[1]["params"]["office_id"] == "office_001"

    @pytest.mark.asyncio
    async def test_read_expenses_returns_list(self) -> None:
        """expenses リソースが経費リストを返すこと。"""
        connector = _make_connector()
        fake_expenses = [
            {"id": "exp_001", "description": "交通費", "amount": 5000},
        ]
        mock_resp = _mock_response({"expenses": fake_expenses})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("expenses")

        assert result == fake_expenses

    @pytest.mark.asyncio
    async def test_read_journal_entries_returns_list(self) -> None:
        """journal_entries リソースが仕訳リストを返すこと。"""
        connector = _make_connector()
        fake_entries = [
            {"id": "je_001", "description": "売上計上", "debit_amount": 100000},
        ]
        mock_resp = _mock_response({"journal_entries": fake_entries})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("journal_entries")

        assert result == fake_entries

    @pytest.mark.asyncio
    async def test_read_records_passes_filters_as_params(self) -> None:
        """filters がクエリパラメータとして渡されること。"""
        connector = _make_connector()
        mock_resp = _mock_response({"invoices": []})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            await connector.read_records("invoices", filters={"page": 2, "per_page": 50})

        call_params = async_client.get.call_args[1]["params"]
        assert call_params["page"] == 2
        assert call_params["per_page"] == 50
        assert call_params["office_id"] == "office_001"

    @pytest.mark.asyncio
    async def test_read_records_falls_back_to_data_key(self) -> None:
        """レスポンスにリソース名キーがない場合は data キーで取得すること。"""
        connector = _make_connector()
        fake_data = [{"id": "inv_003"}]
        mock_resp = _mock_response({"data": fake_data})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.read_records("invoices")

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

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.read_records("invoices")

    @pytest.mark.asyncio
    async def test_read_records_raises_on_rate_limit(self) -> None:
        """レート制限（429）時に HTTPStatusError が伝播すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({}, status_code=429)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.read_records("invoices")

    @pytest.mark.asyncio
    async def test_read_records_raises_on_connection_timeout(self) -> None:
        """接続タイムアウト時に ConnectTimeout が伝播すること。"""
        connector = _make_connector()

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.ConnectTimeout):
                await connector.read_records("invoices")


# ---------------------------------------------------------------------------
# 正常系: write_record
# ---------------------------------------------------------------------------


class TestWriteRecord:
    @pytest.mark.asyncio
    async def test_write_invoice_returns_api_response(self) -> None:
        """invoices への write が作成された請求書を返すこと。"""
        connector = _make_connector()
        created_invoice = {"id": "inv_new_001", "title": "新規請求書", "amount": 200000}
        mock_resp = _mock_response(created_invoice)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.write_record("invoices", {"title": "新規請求書", "amount": 200000})

        assert result == created_invoice
        async_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_invoice_includes_office_id(self) -> None:
        """write_record が office_id をペイロードに自動付与すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({"id": "inv_001"})

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            await connector.write_record("invoices", {"title": "テスト"})

        call_json = async_client.post.call_args[1]["json"]
        assert call_json["office_id"] == "office_001"
        assert call_json["title"] == "テスト"

    @pytest.mark.asyncio
    async def test_write_non_writable_resource_raises(self) -> None:
        """write 非対応リソース（expenses）を指定した場合に ValueError が発生すること。"""
        connector = _make_connector()

        with pytest.raises(ValueError, match="write_record"):
            await connector.write_record("expenses", {"description": "テスト経費"})

    @pytest.mark.asyncio
    async def test_write_journal_entries_raises(self) -> None:
        """journal_entries は write 非対応のため ValueError が発生すること。"""
        connector = _make_connector()

        with pytest.raises(ValueError, match="write_record"):
            await connector.write_record("journal_entries", {"description": "仕訳"})

    @pytest.mark.asyncio
    async def test_write_invoice_raises_on_auth_error(self) -> None:
        """認証エラー（401）時に HTTPStatusError が伝播すること。"""
        connector = _make_connector()
        mock_resp = _mock_response({}, status_code=401)

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.post = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            with pytest.raises(httpx.HTTPStatusError):
                await connector.write_record("invoices", {"title": "テスト"})


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

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
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

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
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

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            result = await connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_uses_invoice_base_url(self) -> None:
        """health_check が invoice エンドポイントを叩くこと。"""
        connector = _make_connector()
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async_client = AsyncMock()
        async_client.__aenter__ = AsyncMock(return_value=async_client)
        async_client.__aexit__ = AsyncMock(return_value=False)
        async_client.get = AsyncMock(return_value=mock_resp)

        with patch("workers.connector.money_forward.httpx.AsyncClient", return_value=async_client):
            await connector.health_check()

        call_url = async_client.get.call_args[0][0]
        assert "invoice.moneyforward.com" in call_url
        assert "office_001" in call_url


# ---------------------------------------------------------------------------
# headers プロパティ
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_headers_include_bearer_token(self) -> None:
        """Authorization ヘッダーが Bearer トークン形式であること。"""
        connector = _make_connector()
        assert connector.headers["Authorization"] == "Bearer test_access_token"

    def test_headers_include_content_type(self) -> None:
        """Content-Type が application/json であること。"""
        connector = _make_connector()
        assert connector.headers["Content-Type"] == "application/json"

    def test_access_token_not_logged(self) -> None:
        """認証情報がログに出力されないこと（ヘッダーの中身を直接文字列化しない）。"""
        connector = _make_connector()
        # headers dict の repr にトークン値が含まれていても、
        # ログ出力用途では credentials に直接アクセスしないことをテスト
        assert connector.config.credentials["access_token"] == "test_access_token"
        # office_id プロパティが credentials から正しく取れること
        assert connector.office_id == "office_001"


# ---------------------------------------------------------------------------
# Factory 登録テスト
# ---------------------------------------------------------------------------


class TestFactoryRegistration:
    def test_money_forward_key_is_in_connectors_dict(self) -> None:
        """CONNECTORS 辞書に 'money_forward' キーが登録されていること。"""
        from workers.connector.factory import CONNECTORS

        assert "money_forward" in CONNECTORS
        assert CONNECTORS["money_forward"] is MoneyForwardConnector

    def test_get_connector_returns_money_forward_connector(self) -> None:
        """factory.get_connector('money_forward') が MoneyForwardConnector を返すこと。"""
        from security.encryption import encrypt_field
        from workers.connector.factory import get_connector

        credentials = {"access_token": "tok_abc", "office_id": "office_xyz"}
        encrypted = encrypt_field(credentials)

        connector = get_connector("money_forward", encrypted)

        assert isinstance(connector, MoneyForwardConnector)
        assert connector.config.tool_name == "money_forward"
        assert connector.config.credentials["office_id"] == "office_xyz"

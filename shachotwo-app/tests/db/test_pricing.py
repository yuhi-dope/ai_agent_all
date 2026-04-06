"""db/pricing.py のユニットテスト。"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from db.pricing import (
    DEFAULT_PRICES,
    get_module_price,
    get_all_module_prices,
    get_discount_rate,
)

COMPANY_ID = "test-company-001"


# ─── get_module_price ────────────────────────────────────────────────────────

class TestGetModulePrice:
    @pytest.mark.asyncio
    async def test_returns_db_price_when_found(self):
        """DBにデータがある場合はDB値を返す。"""
        mock_result = MagicMock()
        mock_result.data = [{"monthly_price": 28_000}]

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.lte.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            price = await get_module_price(COMPANY_ID, "brain")

        assert price == 28_000

    @pytest.mark.asyncio
    async def test_returns_default_when_no_data(self):
        """DBにデータがない場合はデフォルト値を返す。"""
        mock_result = MagicMock()
        mock_result.data = []

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.lte.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            price = await get_module_price(COMPANY_ID, "brain")

        assert price == DEFAULT_PRICES["brain"]  # 30_000

    @pytest.mark.asyncio
    async def test_returns_default_on_db_error(self):
        """DB接続エラー時はデフォルト値にフォールバックする。"""
        with patch("db.pricing.get_service_client", side_effect=Exception("DB接続失敗")):
            price = await get_module_price(COMPANY_ID, "bpo_core")

        assert price == DEFAULT_PRICES["bpo_core"]  # 250_000

    @pytest.mark.asyncio
    async def test_unknown_module_returns_fallback(self):
        """DBにもDEFAULT_PRICESにもないモジュールは100_000を返す。"""
        mock_result = MagicMock()
        mock_result.data = []

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.lte.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            price = await get_module_price(COMPANY_ID, "unknown_module_xyz")

        assert price == 100_000


# ─── get_all_module_prices ───────────────────────────────────────────────────

class TestGetAllModulePrices:
    @pytest.mark.asyncio
    async def test_returns_db_prices_merged_with_defaults(self):
        """DBにbrainのみある場合、bpo_coreはデフォルト補完される。"""
        mock_result = MagicMock()
        mock_result.data = [
            {"module_code": "brain", "monthly_price": 25_000, "valid_from": "2025-01-01"},
        ]

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.lte.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            prices = await get_all_module_prices(COMPANY_ID)

        assert prices["brain"] == 25_000               # DB値
        assert prices["bpo_core"] == DEFAULT_PRICES["bpo_core"]  # デフォルト補完

    @pytest.mark.asyncio
    async def test_deduplicates_by_valid_from_desc(self):
        """同一module_codeが複数ある場合、先頭（valid_from降順）の値を使用する。"""
        mock_result = MagicMock()
        mock_result.data = [
            {"module_code": "brain", "monthly_price": 28_000, "valid_from": "2025-06-01"},
            {"module_code": "brain", "monthly_price": 30_000, "valid_from": "2025-01-01"},
        ]

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.lte.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            prices = await get_all_module_prices(COMPANY_ID)

        assert prices["brain"] == 28_000  # 新しいvalid_fromの値

    @pytest.mark.asyncio
    async def test_returns_defaults_on_db_error(self):
        """DBエラー時はDEFAULT_PRICESをそのまま返す。"""
        with patch("db.pricing.get_service_client", side_effect=Exception("タイムアウト")):
            prices = await get_all_module_prices(COMPANY_ID)

        assert prices == DEFAULT_PRICES


# ─── get_discount_rate ───────────────────────────────────────────────────────

class TestGetDiscountRate:
    @pytest.mark.asyncio
    async def test_returns_db_discount(self):
        """DBに割引データがある場合はDB値を返す。"""
        mock_result = MagicMock()
        mock_result.data = [{
            "discount_code": "annual",
            "discount_type": "rate",
            "rate_percent": "15.00",
            "conditions": {},
        }]

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            discount = await get_discount_rate(COMPANY_ID, "annual")

        assert discount["rate_percent"] == "15.00"
        assert discount["discount_type"] == "rate"

    @pytest.mark.asyncio
    async def test_returns_default_when_no_data(self):
        """DBにデータがない場合はDEFAULT_DISCOUNTSを返す。"""
        mock_result = MagicMock()
        mock_result.data = []

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            discount = await get_discount_rate(COMPANY_ID, "annual")

        assert discount["discount_type"] == "rate"
        assert discount["rate_percent"] == Decimal("10.00")

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_discount(self):
        """DBにもDEFAULT_DISCOUNTSにも存在しない割引コードは空辞書を返す。"""
        mock_result = MagicMock()
        mock_result.data = []

        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_result

        mock_db = MagicMock()
        mock_db.table.return_value = mock_table

        with patch("db.pricing.get_service_client", return_value=mock_db):
            discount = await get_discount_rate(COMPANY_ID, "nonexistent_code")

        assert discount == {}

    @pytest.mark.asyncio
    async def test_returns_default_on_db_error(self):
        """DBエラー時はDEFAULT_DISCOUNTSを返す。"""
        with patch("db.pricing.get_service_client", side_effect=Exception("接続失敗")):
            discount = await get_discount_rate(COMPANY_ID, "annual")

        assert discount["discount_type"] == "rate"

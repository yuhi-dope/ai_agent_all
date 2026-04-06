"""料金マスタ取得ユーティリティ。

pricing_modules / pricing_discounts テーブルからDB管理の料金を取得する。
DBに料金データがない場合はハードコードのデフォルト値にフォールバック。
"""
import logging
from datetime import date
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

try:
    from db.supabase import get_service_client
except Exception:  # pragma: no cover
    get_service_client = None  # type: ignore[assignment]

# デフォルト料金（DB未設定時のフォールバック）
DEFAULT_PRICES: dict[str, int] = {
    "brain": 30_000,
    "bpo_core": 250_000,
    "additional": 100_000,
    "backoffice": 200_000,
}

DEFAULT_DISCOUNTS: dict[str, dict[str, Any]] = {
    "annual": {"discount_type": "rate", "rate_percent": Decimal("10.00")},
    "referral": {"discount_type": "fixed", "fixed_amount": 0},  # 初月無料は別ロジック
}

TAX_RATE = Decimal("0.10")


async def get_module_price(company_id: str, module_code: str) -> int:
    """モジュールの月額料金を取得する。DB未設定時はデフォルト値。

    Args:
        company_id: テナントID
        module_code: "brain" | "bpo_core" | "additional" | "backoffice"

    Returns:
        月額料金（税抜、円）
    """
    try:
        db = get_service_client()
        today = date.today().isoformat()

        result = (
            db.table("pricing_modules")
            .select("monthly_price")
            .eq("company_id", company_id)
            .eq("module_code", module_code)
            .eq("is_active", True)
            .lte("valid_from", today)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            return int(result.data[0]["monthly_price"])
    except Exception as e:
        logger.warning(f"pricing DB fetch failed for {module_code} (using default): {e}")

    return DEFAULT_PRICES.get(module_code, 100_000)


async def get_all_module_prices(company_id: str) -> dict[str, int]:
    """全モジュールの料金を一括取得する。

    同一module_codeが複数ある場合は有効開始日（valid_from）が新しい方を優先する。
    DBにデータがない場合はDEFAULT_PRICESを返す。

    Args:
        company_id: テナントID

    Returns:
        {module_code: monthly_price} の辞書
    """
    try:
        db = get_service_client()
        today = date.today().isoformat()

        result = (
            db.table("pricing_modules")
            .select("module_code, monthly_price, valid_from")
            .eq("company_id", company_id)
            .eq("is_active", True)
            .lte("valid_from", today)
            .order("valid_from", desc=True)
            .execute()
        )

        if result.data:
            prices: dict[str, int] = {}
            for row in result.data:
                code = row["module_code"]
                # valid_from降順ソート済みなので初出のレコードが最新
                if code not in prices:
                    prices[code] = int(row["monthly_price"])
            # DBにないコードはデフォルトで補完
            for code, price in DEFAULT_PRICES.items():
                if code not in prices:
                    prices[code] = price
            return prices
    except Exception as e:
        logger.warning(f"pricing DB bulk fetch failed (using defaults): {e}")

    return dict(DEFAULT_PRICES)


async def get_discount_rate(company_id: str, discount_code: str) -> dict[str, Any]:
    """割引ルールを取得する。

    Args:
        company_id: テナントID
        discount_code: "annual" | "referral" | "volume" | "early_bird"

    Returns:
        割引ルールの辞書。DBになければDEFAULT_DISCOUNTSを返す。
    """
    try:
        db = get_service_client()

        result = (
            db.table("pricing_discounts")
            .select("*")
            .eq("company_id", company_id)
            .eq("discount_code", discount_code)
            .eq("is_active", True)
            .order("valid_from", desc=True)
            .limit(1)
            .execute()
        )

        if result.data:
            return dict(result.data[0])
    except Exception as e:
        logger.warning(f"discount DB fetch failed for {discount_code} (using default): {e}")

    return dict(DEFAULT_DISCOUNTS.get(discount_code, {}))

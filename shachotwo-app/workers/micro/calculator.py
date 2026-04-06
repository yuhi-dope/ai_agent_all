"""cost_calculator マイクロエージェント。数量×単価→金額計算。Decimal使用・LLM不使用。"""
import time
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)


async def run_cost_calculator(input: MicroAgentInput) -> MicroAgentOutput:
    """
    各アイテムの数量×単価を計算してamountを付与する。

    payload:
        items (list[dict]): 計算対象アイテム
            - quantity (float|str): 数量
            - unit_price (float|str|None): 単価
            - category (str): 工種（ログ用）

    result:
        items (list[dict]): amount付きアイテム
        subtotal (int): 合計金額（円、切捨て）
        item_count (int): 全アイテム数
        zero_price_count (int): 単価未設定アイテム数
    """
    start_ms = int(time.time() * 1000)
    agent_name = "cost_calculator"

    try:
        items: list[dict[str, Any]] = input.payload.get("items", [])

        if not items:
            return MicroAgentOutput(
                agent_name=agent_name, success=True,
                result={"items": [], "subtotal": 0, "item_count": 0, "zero_price_count": 0},
                confidence=1.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        result_items = []
        subtotal = Decimal("0")
        zero_price_count = 0

        for item in items:
            item_out = dict(item)
            qty_raw = item.get("quantity")
            price_raw = item.get("unit_price")

            try:
                qty = Decimal(str(qty_raw)) if qty_raw is not None else Decimal("0")
            except InvalidOperation:
                qty = Decimal("0")

            try:
                price = Decimal(str(price_raw)) if price_raw is not None else None
            except InvalidOperation:
                price = None

            if price is not None and price > 0:
                amount = int(qty * price)
                item_out["amount"] = amount
                subtotal += Decimal(str(amount))
            else:
                item_out["amount"] = None
                zero_price_count += 1

            result_items.append(item_out)

        item_count = len(items)
        confidence = (item_count - zero_price_count) / item_count if item_count > 0 else 1.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={
                "items": result_items,
                "subtotal": int(subtotal),
                "item_count": item_count,
                "zero_price_count": zero_price_count,
            },
            confidence=round(confidence, 3), cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"cost_calculator error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

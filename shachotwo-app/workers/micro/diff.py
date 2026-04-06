"""diff_detector マイクロエージェント。before/after辞書の差分を検出する。LLM不使用。"""
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)

# 重要フィールド（これらに変化があればsignificant=True）
_SIGNIFICANT_FIELDS = {
    "total", "total_cost", "amount", "unit_price", "quantity",
    "status", "approved", "contract_amount", "payment_amount",
}


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """ネストされた辞書をフラット化する。"""
    result: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            result.update(_flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            result.update(_flatten(v, key))
    else:
        result[prefix] = obj
    return result


async def run_diff_detector(input: MicroAgentInput) -> MicroAgentOutput:
    """
    before/afterの差分を検出する。

    payload:
        before (dict): 変更前データ
        after (dict): 変更後データ
        context (str, optional): 差分の文脈説明

    result:
        changes (list[dict]): 変更点一覧
        change_count (int): 変更数
        significant (bool): 重要フィールドに変化があるか
    """
    start_ms = int(time.time() * 1000)
    agent_name = "diff_detector"

    try:
        before: dict[str, Any] = input.payload.get("before", {})
        after: dict[str, Any] = input.payload.get("after", {})

        flat_before = _flatten(before)
        flat_after = _flatten(after)

        all_keys = set(flat_before.keys()) | set(flat_after.keys())
        changes: list[dict[str, Any]] = []
        significant = False

        for key in sorted(all_keys):
            v_before = flat_before.get(key)
            v_after = flat_after.get(key)

            if v_before == v_after:
                continue

            change: dict[str, Any] = {
                "field": key,
                "before": v_before,
                "after": v_after,
                "change_rate": None,
            }

            # 数値フィールドは変化率を計算
            try:
                nb = float(v_before) if v_before is not None else None
                na = float(v_after) if v_after is not None else None
                if nb is not None and na is not None and nb != 0:
                    change["change_rate"] = round((na - nb) / abs(nb), 4)
            except (TypeError, ValueError):
                pass

            changes.append(change)

            # 重要フィールドチェック（末尾のフィールド名で判定）
            field_base = key.split(".")[-1].rstrip("]").split("[")[0]
            if field_base in _SIGNIFICANT_FIELDS:
                significant = True

        if changes and not significant:
            significant = len(changes) >= 5  # 変更が5件以上でも重要とみなす

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={
                "changes": changes,
                "change_count": len(changes),
                "significant": significant,
            },
            confidence=1.0, cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"diff_detector error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

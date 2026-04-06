"""テナント別LLMコスト追跡。

月間コスト上限を超えたテナントのLLM呼び出しをブロックする。
MVP: インメモリ追跡。Phase 2+でDBに永続化。

Usage:
    from llm.cost_tracker import CostTracker

    tracker = CostTracker()
    tracker.check_budget(company_id)       # 超過時はHTTPException(429)
    tracker.record_cost(company_id, 0.5)   # ¥0.5を記録
    status = tracker.get_status(company_id) # 現在の使用状況
"""
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# デフォルト月間上限（円）
DEFAULT_MONTHLY_BUDGET_YEN = 50_000

# 警告閾値（上限の80%で警告ログ）
WARNING_THRESHOLD = 0.80


@dataclass
class TenantCostRecord:
    """1テナントの月間コスト記録"""
    total_cost_yen: float = 0.0
    request_count: int = 0
    month_key: str = ""  # "2026-03"
    last_updated: float = 0.0
    budget_yen: float = DEFAULT_MONTHLY_BUDGET_YEN
    warning_sent: bool = False


class CostTracker:
    """テナント別LLMコスト追跡"""

    def __init__(self) -> None:
        self._records: dict[str, TenantCostRecord] = defaultdict(TenantCostRecord)

    def _current_month_key(self) -> str:
        return time.strftime("%Y-%m")

    def _ensure_current_month(self, company_id: str) -> TenantCostRecord:
        """月が変わっていたらリセット"""
        record = self._records[company_id]
        current_month = self._current_month_key()
        if record.month_key != current_month:
            record.total_cost_yen = 0.0
            record.request_count = 0
            record.month_key = current_month
            record.warning_sent = False
        return record

    def check_budget(self, company_id: str) -> None:
        """
        予算チェック。超過時は429 HTTPExceptionをraise。

        Raises:
            HTTPException(429): 月間コスト上限超過
        """
        record = self._ensure_current_month(company_id)
        if record.total_cost_yen >= record.budget_yen:
            logger.error(
                f"LLM budget exceeded: company={company_id} "
                f"cost=¥{record.total_cost_yen:.2f} budget=¥{record.budget_yen:.2f}"
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    f"月間LLMコスト上限（¥{record.budget_yen:,.0f}）を超過しました。"
                    f"現在の使用額: ¥{record.total_cost_yen:,.2f}。"
                    "管理者にお問い合わせください。"
                ),
            )

    def record_cost(self, company_id: str, cost_yen: float) -> None:
        """コストを記録"""
        record = self._ensure_current_month(company_id)
        record.total_cost_yen += cost_yen
        record.request_count += 1
        record.last_updated = time.time()

        # 警告閾値チェック
        if (
            not record.warning_sent
            and record.total_cost_yen >= record.budget_yen * WARNING_THRESHOLD
        ):
            record.warning_sent = True
            logger.warning(
                f"LLM budget warning: company={company_id} "
                f"cost=¥{record.total_cost_yen:.2f} "
                f"({record.total_cost_yen / record.budget_yen:.0%} of ¥{record.budget_yen:,.0f})"
            )

    def get_status(self, company_id: str) -> dict:
        """テナントのコスト状況を取得"""
        record = self._ensure_current_month(company_id)
        remaining = max(0, record.budget_yen - record.total_cost_yen)
        return {
            "company_id": company_id,
            "month": record.month_key,
            "total_cost_yen": round(record.total_cost_yen, 4),
            "budget_yen": record.budget_yen,
            "remaining_yen": round(remaining, 4),
            "usage_rate": round(record.total_cost_yen / record.budget_yen, 4) if record.budget_yen > 0 else 0,
            "request_count": record.request_count,
        }

    def set_budget(self, company_id: str, budget_yen: float) -> None:
        """テナントの月間予算を設定"""
        record = self._ensure_current_month(company_id)
        record.budget_yen = budget_yen

    def reset(self, company_id: str) -> None:
        """テスト用: テナントのコスト記録をリセット"""
        self._records.pop(company_id, None)


# シングルトンインスタンス
_global_tracker: CostTracker | None = None


def get_cost_tracker() -> CostTracker:
    """グローバルCostTrackerを取得"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = CostTracker()
    return _global_tracker

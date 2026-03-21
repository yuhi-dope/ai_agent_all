"""テナント別レート制限。

インメモリのスライディングウィンドウカウンタ。
本番ではRedisに置き換える（MVP段階はインメモリで十分）。

Usage in router:
    from security.rate_limiter import check_rate_limit

    @router.post("/some-endpoint")
    async def endpoint(user=Depends(get_current_user)):
        check_rate_limit(user.company_id, "bpo_pipeline", limit=10, window_seconds=60)
        ...
"""
import time
import logging
from collections import defaultdict
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# テナント別リクエスト履歴: {(company_id, category): [timestamp, ...]}
_request_history: dict[tuple[str, str], list[float]] = defaultdict(list)

# デフォルト制限
RATE_LIMITS: dict[str, dict[str, int]] = {
    "bpo_pipeline": {"limit": 10, "window_seconds": 60},     # BPO: 10req/min
    "auth": {"limit": 5, "window_seconds": 60},               # 認証: 5req/min
    "llm_direct": {"limit": 20, "window_seconds": 60},        # LLM直接: 20req/min
    "default": {"limit": 30, "window_seconds": 60},            # その他: 30req/min
}


def check_rate_limit(
    company_id: str,
    category: str = "default",
    limit: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """
    レート制限チェック。超過時は429 HTTPExceptionをraise。

    Args:
        company_id: テナントID
        category: 制限カテゴリ（bpo_pipeline/auth/llm_direct/default）
        limit: リクエスト上限（Noneの場合はカテゴリデフォルト）
        window_seconds: ウィンドウ秒数（Noneの場合はカテゴリデフォルト）
    """
    config = RATE_LIMITS.get(category, RATE_LIMITS["default"])
    max_requests = limit or config["limit"]
    window = window_seconds or config["window_seconds"]

    key = (company_id, category)
    now = time.time()
    cutoff = now - window

    # 期限切れエントリを削除
    history = _request_history[key]
    _request_history[key] = [t for t in history if t > cutoff]
    history = _request_history[key]

    if len(history) >= max_requests:
        retry_after = int(history[0] + window - now) + 1
        logger.warning(
            f"Rate limit exceeded: company={company_id} category={category} "
            f"count={len(history)}/{max_requests} window={window}s"
        )
        raise HTTPException(
            status_code=429,
            detail=f"リクエスト制限を超過しました。{retry_after}秒後に再試行してください。",
            headers={"Retry-After": str(retry_after)},
        )

    history.append(now)


def reset_rate_limit(company_id: str, category: str = "default") -> None:
    """テスト用: 特定テナント×カテゴリのレート制限をリセット"""
    key = (company_id, category)
    _request_history.pop(key, None)


def get_rate_limit_status(company_id: str, category: str = "default") -> dict:
    """現在のレート制限状況を取得"""
    config = RATE_LIMITS.get(category, RATE_LIMITS["default"])
    key = (company_id, category)
    now = time.time()
    cutoff = now - config["window_seconds"]
    history = [t for t in _request_history.get(key, []) if t > cutoff]
    return {
        "company_id": company_id,
        "category": category,
        "current_count": len(history),
        "limit": config["limit"],
        "window_seconds": config["window_seconds"],
        "remaining": max(0, config["limit"] - len(history)),
    }

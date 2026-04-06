"""LLMモデル状態確認・更新通知・コスト情報エンドポイント。

管理者がモデル登録状況・廃止予定・コスト比較・タスク別推奨を確認できる。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from auth.middleware import get_current_user
from auth.jwt import JWTClaims

logger = logging.getLogger(__name__)

router = APIRouter()

# llm/model_registry.py は別エージェントが作成中のため遅延インポートで対応
def _get_registry_module():
    try:
        import llm.model_registry as reg
        return reg
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="モデルレジストリが利用できません。",
        ) from exc


@router.get("/models/registry")
async def list_models(
    user: JWTClaims = Depends(get_current_user),
) -> list[dict]:
    """登録済みLLMモデル一覧。

    deprecated フラグ・コスト・capabilities を含む。
    """
    reg = _get_registry_module()
    try:
        result = []
        for model_id, info in reg.MODEL_REGISTRY.items():
            costs = reg.get_model_costs(model_id)
            entry = {
                "id": model_id,
                "provider": info.provider,
                "tier": info.tier,
                "deprecated": info.deprecated,
                "cost_per_1k_in": info.cost_per_1k_in,
                "cost_per_1k_out": info.cost_per_1k_out,
                "costs": costs,
            }
            # capabilitiesがあれば含める
            if hasattr(info, "capabilities"):
                entry["capabilities"] = info.capabilities
            result.append(entry)
        return result
    except Exception as exc:
        logger.exception("list_models failed")
        raise HTTPException(status_code=500, detail="モデル一覧の取得に失敗しました。") from exc


@router.get("/models/status")
async def model_status(
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """モデル状態サマリー。

    アクティブ数・廃止予定数・フォールバックチェインを返す。
    """
    reg = _get_registry_module()
    try:
        active_models = [
            model_id
            for model_id, info in reg.MODEL_REGISTRY.items()
            if not info.deprecated
        ]
        deprecated_models = [
            model_id
            for model_id, info in reg.MODEL_REGISTRY.items()
            if info.deprecated
        ]
        return {
            "active_models": active_models,
            "deprecated_models": deprecated_models,
            "fallback_chains": {
                "fast": reg.get_fallback_chain("fast"),
                "standard": reg.get_fallback_chain("standard"),
                "premium": reg.get_fallback_chain("premium"),
            },
            "deprecation_warnings": reg.check_deprecations(),
        }
    except Exception as exc:
        logger.exception("model_status failed")
        raise HTTPException(status_code=500, detail="モデル状態の取得に失敗しました。") from exc


@router.get("/models/updates")
async def model_updates(
    limit: int = 20,
    user: JWTClaims = Depends(get_current_user),
) -> list[dict]:
    """モデル更新ログ。

    追加・廃止・コスト変更などの履歴を返す。
    """
    reg = _get_registry_module()
    try:
        logs = reg.get_update_logs(limit=limit)
        # ModelUpdateLog が dataclass/Pydantic どちらでも dict 変換する
        result = []
        for log in logs:
            if isinstance(log, dict):
                result.append(log)
            elif hasattr(log, "model_dump"):
                result.append(log.model_dump())
            elif hasattr(log, "__dict__"):
                result.append(log.__dict__)
            else:
                result.append(str(log))
        return result
    except Exception as exc:
        logger.exception("model_updates failed")
        raise HTTPException(status_code=500, detail="モデル更新ログの取得に失敗しました。") from exc


@router.get("/models/cost-comparison")
async def cost_comparison(
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """全モデルのコスト比較表とタスク別推奨モデル。"""
    reg = _get_registry_module()
    try:
        # アクティブモデルのコスト一覧
        models = [
            {
                "id": info.id,
                "provider": info.provider,
                "cost_in": info.cost_per_1k_in,
                "cost_out": info.cost_per_1k_out,
                "tier": info.tier,
            }
            for info in reg.MODEL_REGISTRY.values()
            if not info.deprecated
        ]

        # タスク別推奨モデル
        fast_tasks = ("extraction", "qa")
        task_types = [
            "extraction",
            "qa",
            "reasoning",
            "critical_decision",
            "structured_output",
        ]
        recommendations: dict[str, str] = {}
        for task in task_types:
            tier = "fast" if task in fast_tasks else "standard"
            try:
                recommendations[task] = reg.select_optimal_model(
                    tier=tier,
                    task_type=task,
                )
            except Exception:
                logger.warning("select_optimal_model failed for task=%s", task)
                recommendations[task] = "unknown"

        return {
            "models": models,
            "recommendations": recommendations,
        }
    except Exception as exc:
        logger.exception("cost_comparison failed")
        raise HTTPException(status_code=500, detail="コスト比較の取得に失敗しました。") from exc


@router.get("/models/recommend")
async def recommend_model(
    task_type: str = "general",
    requires_vision: bool = False,
    requires_structured_output: bool = False,
    max_cost_per_1k_out: Optional[float] = None,
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """タスク要件に基づく最適モデル推奨。

    Args:
        task_type: タスク種別（例: extraction / qa / reasoning / critical_decision）
        requires_vision: 画像入力が必要かどうか
        requires_structured_output: 構造化出力（JSON）が必要かどうか
        max_cost_per_1k_out: 出力1kトークンあたりの最大コスト上限（USD）
    """
    reg = _get_registry_module()
    try:
        # task_type に応じてデフォルト tier を決定
        fast_tasks = ("extraction", "qa", "summary")
        premium_tasks = ("critical_decision",)
        if task_type in fast_tasks:
            tier = "fast"
        elif task_type in premium_tasks:
            tier = "premium"
        else:
            tier = "standard"

        model_id = reg.select_optimal_model(
            tier=tier,
            task_type=task_type,
            requires_vision=requires_vision,
            requires_structured_output=requires_structured_output,
            max_cost_per_1k_out=max_cost_per_1k_out,
        )
        info = reg.MODEL_REGISTRY.get(model_id)
        costs = reg.get_model_costs(model_id) if info else {}

        return {
            "recommended_model": model_id,
            "tier": tier,
            "task_type": task_type,
            "requires_vision": requires_vision,
            "requires_structured_output": requires_structured_output,
            "max_cost_per_1k_out": max_cost_per_1k_out,
            "costs": costs,
        }
    except Exception as exc:
        logger.exception("recommend_model failed: task_type=%s", task_type)
        raise HTTPException(status_code=500, detail="モデル推奨の取得に失敗しました。") from exc

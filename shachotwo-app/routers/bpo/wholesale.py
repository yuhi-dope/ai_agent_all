"""卸売業BPO FastAPIルーター

エンドポイント:
  POST /wholesale/pipelines/{pipeline_name}  — パイプライン汎用実行
  GET  /wholesale/pipelines                  — 利用可能パイプライン一覧

対応パイプライン:
  order_processing      受発注AI
  inventory_management  在庫・倉庫管理
  accounts_receivable   請求・売掛管理
  accounts_payable      仕入・買掛管理
  shipping              物流・配送管理
  sales_intelligence    営業支援
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from security.rate_limiter import check_rate_limit
from workers.bpo.wholesale.pipelines import PIPELINE_REGISTRY, get_pipeline_runner

logger = logging.getLogger(__name__)
router = APIRouter()


class PipelineRunRequest(BaseModel):
    """パイプライン実行リクエスト"""
    input_data: dict[str, Any]


class PipelineRunResponse(BaseModel):
    """パイプライン実行レスポンス"""
    success: bool
    pipeline: str
    output: dict[str, Any] = {}
    cost_yen: float = 0.0
    duration_ms: int = 0
    failed_step: str | None = None
    steps_summary: list[dict[str, Any]] = []


class PipelineListResponse(BaseModel):
    """パイプライン一覧レスポンス"""
    industry: str
    pipelines: list[dict[str, Any]]


@router.get("/pipelines", response_model=PipelineListResponse)
async def list_pipelines(
    user=Depends(get_current_user),
) -> PipelineListResponse:
    """利用可能な卸売業BPOパイプラインの一覧を返す"""
    pipelines = [
        {
            "pipeline_id": pid,
            "description": meta["description"],
            "steps": meta["steps"],
            "status": meta["status"],
            "phase": meta.get("phase", ""),
            "tier": meta.get("tier", ""),
        }
        for pid, meta in PIPELINE_REGISTRY.items()
    ]
    return PipelineListResponse(industry="wholesale", pipelines=pipelines)


@router.post("/pipelines/{pipeline_name}", response_model=PipelineRunResponse)
async def run_wholesale_pipeline(
    pipeline_name: str,
    body: PipelineRunRequest,
    user=Depends(get_current_user),
) -> PipelineRunResponse:
    """
    卸売業BPOパイプラインを実行する。

    pipeline_name に指定可能な値:
      - order_processing
      - inventory_management
      - accounts_receivable
      - accounts_payable
      - shipping
      - sales_intelligence
    """
    if pipeline_name not in PIPELINE_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=(
                f"パイプライン '{pipeline_name}' は存在しません。"
                f"利用可能: {list(PIPELINE_REGISTRY.keys())}"
            ),
        )

    check_rate_limit(user.company_id, "bpo_pipeline")

    try:
        runner = get_pipeline_runner(pipeline_name)
    except (KeyError, AttributeError) as exc:
        logger.error(f"wholesale pipeline runner load failed: {exc}")
        raise HTTPException(status_code=500, detail=f"パイプライン読み込みエラー: {exc}")

    try:
        result = await runner(
            company_id=user.company_id,
            input_data=body.input_data,
        )
    except Exception as exc:
        logger.exception(f"wholesale/{pipeline_name} pipeline error: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"パイプライン実行エラー: {exc}",
        )

    if not result.success:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"パイプライン '{pipeline_name}' が失敗しました",
                "failed_step": result.failed_step,
            },
        )

    steps_summary = [
        {
            "step_no": s.step_no,
            "step_name": s.step_name,
            "agent_name": s.agent_name,
            "success": s.success,
            "confidence": s.confidence,
            "cost_yen": s.cost_yen,
            "duration_ms": s.duration_ms,
            "warning": s.warning,
        }
        for s in result.steps
    ]

    return PipelineRunResponse(
        success=result.success,
        pipeline=f"wholesale/{pipeline_name}",
        output=result.final_output,
        cost_yen=result.total_cost_yen,
        duration_ms=result.total_duration_ms,
        failed_step=result.failed_step,
        steps_summary=steps_summary,
    )

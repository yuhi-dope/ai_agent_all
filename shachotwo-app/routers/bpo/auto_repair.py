"""自動車整備BPO FastAPIルーター"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from security.rate_limiter import check_rate_limit
from workers.bpo.manager.models import BPOTask, ExecutionLevel, TriggerType
from workers.bpo.manager.task_router import route_and_execute

logger = logging.getLogger(__name__)
router = APIRouter()


class PipelineRunRequest(BaseModel):
    """パイプライン実行リクエスト"""
    input_data: dict[str, Any]
    execution_level: int = 2  # DRAFT_CREATE


class PipelineRunResponse(BaseModel):
    success: bool
    pipeline: str
    output: dict[str, Any] = {}
    cost_yen: float = 0.0
    duration_ms: int = 0


@router.post("/repair_quoting/run", response_model=PipelineRunResponse)
async def run_repair_quoting_pipeline(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """見積・請求パイプライン実行"""
    check_rate_limit(user.company_id, "bpo_pipeline")
    task = BPOTask(
        company_id=user.company_id,
        pipeline="auto_repair/repair_quoting",
        trigger_type=TriggerType.EVENT,
        execution_level=ExecutionLevel(body.execution_level),
        input_data=body.input_data,
    )
    result = await route_and_execute(task)
    if not result.success:
        raise HTTPException(
            status_code=422,
            detail=result.final_output.get("error", "Pipeline failed"),
        )
    return PipelineRunResponse(
        success=result.success,
        pipeline=result.pipeline,
        output=result.final_output,
        cost_yen=result.total_cost_yen,
        duration_ms=result.total_duration_ms,
    )

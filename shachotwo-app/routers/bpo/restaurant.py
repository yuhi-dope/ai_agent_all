"""飲食業BPO FastAPIルーター"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.middleware import get_current_user
from db.supabase import get_service_client
from workers.bpo.manager.models import BPOTask, ExecutionLevel, TriggerType
from workers.bpo.manager.task_router import route_and_execute

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# リクエスト / レスポンスモデル
# ─────────────────────────────────────

class FLCostCalculateRequest(BaseModel):
    """FLコスト計算リクエスト"""
    target_month: str               # 例: "202603"
    food_cost: float                # 食材費（円）
    labor_cost: float               # 人件費（円）
    sales: Optional[float] = None  # 売上（円）


class FLCostCalculateResponse(BaseModel):
    success: bool
    output: dict


class FLCostHistoryItem(BaseModel):
    target_month: str
    fl_ratio: Optional[float]
    food_cost: Optional[float]
    labor_cost: Optional[float]
    created_at: Optional[str]


class ShiftPlanRequest(BaseModel):
    """シフト計画作成リクエスト"""
    target_month: str               # 例: "202604"
    staff_count: int                # スタッフ数
    business_hours: Optional[str] = None  # 例: "11:00-23:00"
    notes: Optional[str] = None


class ShiftPlanResponse(BaseModel):
    success: bool
    output: dict


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.post("/fl-cost/calculate", response_model=FLCostCalculateResponse)
async def calculate_fl_cost(
    body: FLCostCalculateRequest,
    user=Depends(get_current_user),
):
    """FLコスト計算"""
    task = BPOTask(
        pipeline="restaurant/fl_cost",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"restaurant/fl_cost 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return FLCostCalculateResponse(
        success=result.success,
        output=result.final_output,
    )


@router.get("/fl-cost/history", response_model=list[FLCostHistoryItem])
async def list_fl_cost_history(
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    user=Depends(get_current_user),
):
    """FLコスト計算履歴"""
    try:
        client = get_service_client()
        result = client.table("execution_logs").select(
            "id, operations, overall_success, created_at"
        ).eq(
            "company_id", str(user.company_id)
        ).order(
            "created_at", desc=True
        ).range(offset, offset + limit - 1).execute()

        items = []
        for row in (result.data or []):
            ops = row.get("operations") or {}
            if ops.get("pipeline") != "restaurant/fl_cost":
                continue
            input_data = ops.get("input_data", {})
            final_output = ops.get("final_output", {})
            items.append(FLCostHistoryItem(
                target_month=input_data.get("target_month", ""),
                fl_ratio=final_output.get("fl_ratio"),
                food_cost=input_data.get("food_cost"),
                labor_cost=input_data.get("labor_cost"),
                created_at=row.get("created_at"),
            ))
        return items
    except Exception as e:
        logger.error(f"list_fl_cost_history 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/shifts/plan", response_model=ShiftPlanResponse)
async def plan_shifts(
    body: ShiftPlanRequest,
    user=Depends(get_current_user),
):
    """シフト計画作成"""
    task = BPOTask(
        pipeline="restaurant/shift",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"restaurant/shift 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ShiftPlanResponse(
        success=result.success,
        output=result.final_output,
    )

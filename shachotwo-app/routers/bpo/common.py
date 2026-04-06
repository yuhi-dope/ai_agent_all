"""共通バックオフィスBPO FastAPIルーター"""
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

class AttendanceProcessRequest(BaseModel):
    """勤怠処理リクエスト"""
    target_month: str               # 例: "202603"
    department: Optional[str] = None
    employee_ids: Optional[list[str]] = None
    notes: Optional[str] = None


class AttendanceProcessResponse(BaseModel):
    success: bool
    output: dict


class ContractAnalyzeRequest(BaseModel):
    """契約書分析リクエスト"""
    contract_text: str
    contract_type: Optional[str] = None   # 例: "業務委託", "賃貸"
    counterparty: Optional[str] = None
    notes: Optional[str] = None


class ContractAnalyzeResponse(BaseModel):
    success: bool
    output: dict


class ContractListItem(BaseModel):
    id: str
    counterparty: Optional[str]
    contract_type: Optional[str]
    risk_level: Optional[str]
    risk_alerts: Optional[list[str]]
    created_at: Optional[str]


class ExpenseProcessRequest(BaseModel):
    """経費処理リクエスト"""
    amount: float
    category: str                          # 例: "交通費", "接待費"
    description: str
    receipt_date: Optional[str] = None
    payee: Optional[str] = None


class ExpenseProcessResponse(BaseModel):
    success: bool
    output: dict


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.post("/attendance/process", response_model=AttendanceProcessResponse)
async def process_attendance(
    body: AttendanceProcessRequest,
    user=Depends(get_current_user),
):
    """勤怠処理"""
    task = BPOTask(
        pipeline="common/attendance",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"common/attendance 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return AttendanceProcessResponse(
        success=result.success,
        output=result.final_output,
    )


@router.post("/contracts/analyze", response_model=ContractAnalyzeResponse)
async def analyze_contract(
    body: ContractAnalyzeRequest,
    user=Depends(get_current_user),
):
    """契約書分析"""
    task = BPOTask(
        pipeline="common/contract",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"common/contract 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ContractAnalyzeResponse(
        success=result.success,
        output=result.final_output,
    )


@router.get("/contracts", response_model=list[ContractListItem])
async def list_contracts(
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    user=Depends(get_current_user),
):
    """契約書一覧（リスクアラート付き）"""
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
            if ops.get("pipeline") != "common/contract":
                continue
            input_data = ops.get("input_data", {})
            final_output = ops.get("final_output", {})
            items.append(ContractListItem(
                id=row["id"],
                counterparty=input_data.get("counterparty"),
                contract_type=input_data.get("contract_type"),
                risk_level=final_output.get("risk_level"),
                risk_alerts=final_output.get("risk_alerts"),
                created_at=row.get("created_at"),
            ))
        return items
    except Exception as e:
        logger.error(f"list_contracts 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/expense/process", response_model=ExpenseProcessResponse)
async def process_expense(
    body: ExpenseProcessRequest,
    user=Depends(get_current_user),
):
    """経費処理"""
    task = BPOTask(
        pipeline="common/expense",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"common/expense 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ExpenseProcessResponse(
        success=result.success,
        output=result.final_output,
    )

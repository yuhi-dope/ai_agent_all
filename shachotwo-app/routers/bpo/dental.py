"""歯科BPO FastAPIルーター"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.middleware import get_current_user
from db.supabase import get_service_client
from security.rate_limiter import check_rate_limit
from workers.bpo.manager.models import BPOTask, ExecutionLevel, TriggerType
from workers.bpo.manager.task_router import route_and_execute

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# リクエスト / レスポンスモデル
# ─────────────────────────────────────

class DentalReceiptCheckRequest(BaseModel):
    """レセプトチェックリクエスト"""
    receipt_month: str                        # 例: "202603"
    patient_count: Optional[int] = None      # 対象患者数
    file_path: Optional[str] = None          # レセプトファイルパス
    notes: Optional[str] = None


class DentalReceiptCheckResponse(BaseModel):
    success: bool
    output: dict


class DentalReceiptHistoryItem(BaseModel):
    receipt_month: str
    patient_count: Optional[int]
    status: str
    created_at: Optional[str]
    result_summary: Optional[dict]


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.post("/receipts/check", response_model=DentalReceiptCheckResponse)
async def check_dental_receipt(
    body: DentalReceiptCheckRequest,
    user=Depends(get_current_user),
):
    """レセプトチェック実行"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    task = BPOTask(
        pipeline="dental/receipt_check",
        company_id=str(user.company_id),
        input_data=body.model_dump(),
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"dental/receipt_check 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return DentalReceiptCheckResponse(
        success=result.success,
        output=result.final_output,
    )


@router.get("/receipts", response_model=list[DentalReceiptHistoryItem])
async def list_dental_receipts(
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    user=Depends(get_current_user),
):
    """レセプトチェック履歴一覧"""
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
            if ops.get("pipeline") != "dental/receipt_check":
                continue
            input_data = ops.get("final_output", {})
            items.append(DentalReceiptHistoryItem(
                receipt_month=ops.get("input_data", {}).get("receipt_month", ""),
                patient_count=ops.get("input_data", {}).get("patient_count"),
                status="success" if row.get("overall_success") else "failed",
                created_at=row.get("created_at"),
                result_summary=input_data,
            ))
        return items
    except Exception as e:
        logger.error(f"list_dental_receipts 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

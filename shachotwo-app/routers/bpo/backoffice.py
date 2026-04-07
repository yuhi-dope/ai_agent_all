"""バックオフィスBPOルーター — 経理/労務/人事/総務/調達/法務/IT管理"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role, require_min_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel
from workers.bpo.manager.task_router import route_and_execute

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Pydanticモデル ─────────────────────────────────────

class EmployeeCreate(BaseModel):
    name: str
    department: Optional[str] = None
    position: Optional[str] = None
    employment_type: Optional[str] = None
    hire_date: Optional[date] = None
    base_salary: Optional[int] = None
    hourly_wage: Optional[int] = None


class AttendanceUpsert(BaseModel):
    employee_id: str
    work_date: date
    clock_in: Optional[str] = None
    clock_out: Optional[str] = None
    break_minutes: Optional[int] = None
    status: Optional[str] = None
    note: Optional[str] = None


# ── 経理 ───────────────────────────────────────────────

@router.post("/invoice-issue")
async def run_invoice_issue(body: dict, user=Depends(get_current_user)):
    """請求書発行"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/invoice_issue",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/invoice_issue 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/ar-management")
async def run_ar_management(body: dict, user=Depends(get_current_user)):
    """売掛管理・入金消込"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/ar_management",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DATA_COLLECT,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/ar_management 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/ap-management")
async def run_ap_management(body: dict, user=Depends(get_current_user)):
    """買掛管理・支払処理"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/ap_management",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/ap_management 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/bank-reconciliation")
async def run_bank_reconciliation(body: dict, user=Depends(get_current_user)):
    """銀行照合"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/bank_reconciliation",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DATA_COLLECT,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/bank_reconciliation 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/journal-entry")
async def run_journal_entry(body: dict, user=Depends(get_current_user)):
    """仕訳入力"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/journal_entry",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/journal_entry 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/monthly-close")
async def run_monthly_close(body: dict, user=Depends(get_current_user)):
    """月次決算"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/monthly_close",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/monthly_close 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/tax-filing")
async def run_tax_filing(body: dict, user=Depends(get_current_user)):
    """税務申告支援"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/tax_filing",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/tax_filing 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── 労務 ───────────────────────────────────────────────

@router.post("/social-insurance")
async def run_social_insurance(body: dict, user=Depends(get_current_user)):
    """社会保険届出"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/social_insurance",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/social_insurance 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/year-end-adjustment")
async def run_year_end_adjustment(body: dict, user=Depends(get_current_user)):
    """年末調整"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/year_end_adjustment",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.APPROVAL_GATED,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/year_end_adjustment 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/labor-compliance")
async def run_labor_compliance(body: dict, user=Depends(get_current_user)):
    """労務コンプライアンスチェック"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/labor_compliance",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.NOTIFY_ONLY,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/labor_compliance 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── 人事 ───────────────────────────────────────────────

@router.post("/recruitment")
async def run_recruitment(body: dict, user=Depends(get_current_user)):
    """採用パイプライン"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/recruitment",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/recruitment 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/employee-onboarding")
async def run_employee_onboarding(body: dict, user=Depends(get_current_user)):
    """入社手続き"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/employee_onboarding",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/employee_onboarding 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/employee-offboarding")
async def run_employee_offboarding(body: dict, user=Depends(get_current_user)):
    """退社手続き"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/employee_offboarding",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/employee_offboarding 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── 調達 ───────────────────────────────────────────────

@router.post("/purchase-order")
async def run_purchase_order(body: dict, user=Depends(get_current_user)):
    """発注・検収"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/purchase_order",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/purchase_order 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── 法務 ───────────────────────────────────────────────

@router.post("/compliance-check")
async def run_compliance_check(body: dict, user=Depends(get_current_user)):
    """コンプライアンスチェック"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/compliance_check",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.NOTIFY_ONLY,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/compliance_check 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


@router.post("/antisocial-screening")
async def run_antisocial_screening(body: dict, user=Depends(get_current_user)):
    """反社チェック"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/antisocial_screening",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DRAFT_CREATE,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/antisocial_screening 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── IT管理 ─────────────────────────────────────────────

@router.post("/account-lifecycle")
async def run_account_lifecycle(body: dict, user=Depends(get_current_user)):
    """アカウントライフサイクル管理"""
    task = BPOTask(
        company_id=str(user.company_id),
        pipeline="backoffice/account_lifecycle",
        trigger_type=TriggerType.CONDITION,
        execution_level=ExecutionLevel.DATA_COLLECT,
        input_data=body,
    )
    try:
        result = await route_and_execute(task)
    except Exception as e:
        logger.error(f"backoffice/account_lifecycle 実行エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return result.model_dump()


# ── 従業員・勤怠 CRUD ──────────────────────────────────

@router.get("/employees")
async def list_employees(
    is_active: bool = True,
    department: Optional[str] = None,
    employment_type: Optional[str] = None,
    user: JWTClaims = Depends(require_min_role("editor")),
):
    """従業員一覧"""
    try:
        db = get_service_client()
        query = (
            db.table("employees")
            .select("*", count="exact")
            .eq("company_id", user.company_id)
            .eq("is_active", is_active)
        )
        if department:
            query = query.eq("department", department)
        if employment_type:
            query = query.eq("employment_type", employment_type)
        result = query.order("name").execute()
        return {"items": result.data or [], "total": result.count or 0}
    except Exception as e:
        logger.error(f"list_employees エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/employees")
async def create_employee(
    body: EmployeeCreate,
    user: JWTClaims = Depends(require_role("admin")),
):
    """従業員登録"""
    try:
        db = get_service_client()
        payload = body.model_dump(exclude_none=True)
        payload["company_id"] = str(user.company_id)
        # date を ISO文字列に変換
        if payload.get("hire_date"):
            payload["hire_date"] = str(payload["hire_date"])
        result = db.table("employees").insert(payload).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="従業員の登録に失敗しました")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create_employee エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/attendance/summary")
async def get_attendance_summary(
    month: Optional[str] = None,
    user: JWTClaims = Depends(require_min_role("editor")),
):
    """勤怠月次サマリー（attendance_monthly_summary ビュー）"""
    from datetime import datetime
    if not month:
        month = datetime.now().strftime("%Y-%m")
    try:
        db = get_service_client()
        result = (
            db.table("attendance_monthly_summary")
            .select("*")
            .eq("company_id", user.company_id)
            .eq("month", month)
            .execute()
        )
        return {"items": result.data or [], "month": month}
    except Exception as e:
        logger.error(f"get_attendance_summary エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/attendance")
async def upsert_attendance(
    body: AttendanceUpsert,
    user: JWTClaims = Depends(require_min_role("editor")),
):
    """勤怠登録/更新（employee_id + work_date で upsert）"""
    try:
        db = get_service_client()
        payload = body.model_dump(exclude_none=True)
        payload["company_id"] = str(user.company_id)
        # date を ISO文字列に変換
        payload["work_date"] = str(payload["work_date"])
        result = (
            db.table("attendance_records")
            .upsert([payload], on_conflict="employee_id,work_date")
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=500, detail="勤怠の登録/更新に失敗しました")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"upsert_attendance エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

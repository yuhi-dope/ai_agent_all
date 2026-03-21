"""BPO execution endpoints."""
import logging
import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit import audit_log
from security.rate_limiter import check_rate_limit
from workers.bpo.manager.models import BPOTask, ExecutionLevel, TriggerType
from workers.bpo.manager.task_router import route_and_execute, PIPELINE_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────
# リクエスト / レスポンスモデル
# ─────────────────────────────────────

class BPORunRequest(BaseModel):
    pipeline: str                        # 例: "construction/estimation"
    input_data: dict = {}                # パイプライン固有の入力
    context: dict = {}                   # 追加コンテキスト
    trigger_type: str = "user"           # "user" | "schedule" | "event"
    execution_level: int = 2             # ExecutionLevel enum値 (0-4)
    estimated_impact: float = 0.5
    knowledge_item_ids: list[str] = []
    force_dry_run: bool = False


class BPORunResponse(BaseModel):
    execution_id: str
    pipeline: str
    success: bool
    approval_pending: bool = False
    steps: list[dict] = []
    final_output: dict = {}
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    message: str | None = None


class ExecutionLogResponse(BaseModel):
    id: UUID
    flow_id: Optional[UUID]
    triggered_by: Optional[str]
    operations: dict
    overall_success: Optional[bool]
    time_saved_minutes: Optional[int]
    cost_saved_yen: Optional[int]
    created_at: datetime


class ExecutionLogListResponse(BaseModel):
    items: list[ExecutionLogResponse]
    total: int
    has_more: bool = False


# ─────────────────────────────────────
# HitL モデル
# ─────────────────────────────────────

PIPELINE_LABELS: dict[str, str] = {
    # 建設業
    "construction/estimation":    "建設業 見積書",
    "construction/billing":       "建設業 請求書",
    "construction/safety":        "建設業 安全書類",
    "construction/cost_report":   "建設業 原価報告",
    "construction/subcontractor": "建設業 下請管理",
    "construction/permit":        "建設業 許可申請",
    "construction/photo_organize":"建設業 施工写真整理",
    # 製造業（pipeline_key は manufacturing/quoting で統一）
    "manufacturing/quoting":      "製造業 見積書",
    "manufacturing/estimation":   "製造業 見積書",   # 旧キー互換
    # 共通BPO
    "common/expense":             "経費精算",
    "common/payroll":             "給与計算",
    "common/contract":            "契約書",
    "common/attendance":          "勤怠集計",
    "common/vendor":              "取引先マスタ",
    "common/admin_reminder":      "管理リマインダ",
    # その他業種
    "restaurant/fl_cost":         "飲食業 FL原価計算",
    "restaurant/shift":           "飲食業 シフト",
    "beauty/recall":              "美容 リコール",
    "staffing/dispatch_contract": "派遣契約書",
    "architecture/building_permit":"建築確認申請",
    "clinic/medical_receipt":     "医療 レセプト",
    "hotel/revenue_mgmt":         "ホテル 収益管理",
    "pharmacy/dispensing_billing":"薬局 調剤報酬",
    "dental/receipt_check":       "歯科 レセプト点検",
    "dental/receipt":             "歯科 レセプト",    # 旧キー互換
    "realestate/rent_collection": "不動産 家賃管理",
    "realestate/property":        "不動産 物件管理",  # 旧キー互換
    "professional/deadline_mgmt": "士業 期限管理",
    "professional/deadline":      "士業 期限管理",    # 旧キー互換
    "nursing/care_billing":       "介護 報酬請求",
    "logistics/dispatch":         "物流 配車",
    "ecommerce/listing":          "EC 商品登録",
    "auto_repair/repair_quoting": "整備 見積書",
}


class PendingApprovalItem(BaseModel):
    id: str
    pipeline_key: str
    pipeline_label: str
    created_at: datetime
    summary: str
    confidence: float


class PendingApprovalsResponse(BaseModel):
    count: int
    items: list[PendingApprovalItem]


class ApproveRequest(BaseModel):
    modified_output: dict | None = None  # 修正して承認する場合


class RejectRequest(BaseModel):
    reason: str = ""


# ─────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────

def _resolve_trigger_type(raw: str) -> TriggerType:
    """ユーザー入力文字列を TriggerType に変換する。未知の値は CONDITION へ。"""
    mapping = {
        "user": TriggerType.CONDITION,
        "schedule": TriggerType.SCHEDULE,
        "event": TriggerType.EVENT,
        "proactive": TriggerType.PROACTIVE,
        "condition": TriggerType.CONDITION,
    }
    return mapping.get(raw.lower(), TriggerType.CONDITION)


def _resolve_execution_level(value: int) -> ExecutionLevel:
    """整数を ExecutionLevel に変換する。範囲外は DRAFT_CREATE (2) へ。"""
    try:
        return ExecutionLevel(value)
    except ValueError:
        return ExecutionLevel.DRAFT_CREATE


def _save_execution_log(
    db,
    company_id: str,
    pipeline: str,
    triggered_by: str,
    result_steps: list[dict],
    final_output: dict,
    overall_success: bool,
) -> tuple[str, bool]:
    """execution_logs テーブルに実行ログを保存して (log_id, approval_pending) を返す。

    bpo_hitl_requirements テーブルを参照して approval_status を決定する。
    - requires_approval=True  → approval_status='pending',  original_output に final_output を保存
    - requires_approval=False → approval_status='approved'
    """
    # HitL 設定を取得
    try:
        hitl_row = (
            db.table("bpo_hitl_requirements")
            .select("requires_approval, min_confidence_for_auto")
            .eq("pipeline_key", pipeline)
            .maybe_single()
            .execute()
        )
        requires_approval = (
            hitl_row.data.get("requires_approval", False)
            if hitl_row.data
            else False
        )
    except Exception:
        requires_approval = False

    approval_status = "pending" if requires_approval else "approved"

    log_id = str(uuid.uuid4())
    insert_payload: dict = {
        "id": log_id,
        "company_id": company_id,
        "flow_id": None,
        "triggered_by": triggered_by,
        "operations": {
            "pipeline": pipeline,
            "steps": result_steps,
            "final_output": final_output,
        },
        "overall_success": overall_success,
        "time_saved_minutes": None,
        "cost_saved_yen": None,
        "lessons_learned": None,
        "approval_status": approval_status,
    }
    if requires_approval:
        insert_payload["original_output"] = final_output

    db.table("execution_logs").insert(insert_payload).execute()
    return log_id, requires_approval


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.post("/execution/bpo", response_model=BPORunResponse)
async def run_bpo_pipeline(
    body: BPORunRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """BPOパイプライン実行（admin のみ）。

    1. BPOTask を組み立てる
    2. task_router.route_and_execute() に委譲
    3. execution_logs へ保存
    4. BPORunResponse を返す
    """
    check_rate_limit(user.company_id, "bpo_pipeline")

    # パイプライン登録確認（未登録なら早期 422 を返す）
    if body.pipeline not in PIPELINE_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"パイプライン '{body.pipeline}' は未登録です。PIPELINE_REGISTRY を確認してください。",
        )

    task = BPOTask(
        company_id=str(user.company_id),
        pipeline=body.pipeline,
        trigger_type=_resolve_trigger_type(body.trigger_type),
        execution_level=_resolve_execution_level(body.execution_level),
        input_data=body.input_data,
        estimated_impact=body.estimated_impact,
        knowledge_item_ids=body.knowledge_item_ids,
        context=body.context,
    )

    try:
        result = await route_and_execute(
            task=task,
            force_dry_run=body.force_dry_run,
        )
    except Exception as e:
        logger.error(f"route_and_execute 例外: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # execution_logs 保存（失敗しても本体レスポンスはブロックしない）
    execution_id = str(uuid.uuid4())
    approval_pending_db = False
    try:
        db = get_service_client()
        saved_id, approval_pending_db = _save_execution_log(
            db=db,
            company_id=str(user.company_id),
            pipeline=body.pipeline,
            triggered_by=str(user.sub),
            result_steps=result.steps,
            final_output=result.final_output,
            overall_success=result.success,
        )
        execution_id = saved_id
    except Exception as e:
        logger.warning(f"execution_logs 保存失敗: {e}")

    approval_pending_final = result.approval_pending or approval_pending_db

    await audit_log(
        company_id=str(user.company_id),
        user_id=str(user.sub),
        action="bpo_executed",
        resource_type="execution_log",
        resource_id=execution_id,
        details={
            "pipeline": body.pipeline,
            "success": result.success,
            "approval_pending": approval_pending_final,
            "total_cost_yen": result.total_cost_yen,
        },
    )

    message = result.final_output.get("message") if result.final_output else None

    return BPORunResponse(
        execution_id=execution_id,
        pipeline=result.pipeline,
        success=result.success,
        approval_pending=approval_pending_final,
        steps=result.steps,
        final_output=result.final_output,
        total_cost_yen=result.total_cost_yen,
        total_duration_ms=result.total_duration_ms,
        failed_step=result.failed_step,
        message=message,
    )


@router.get("/execution/logs", response_model=ExecutionLogListResponse)
async def list_execution_logs(
    flow_id: Optional[UUID] = None,
    overall_success: Optional[bool] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """実行ログ一覧（company_id フィルタ + ページネーション）"""
    try:
        db = get_service_client()

        q = db.table("execution_logs") \
            .select(
                "id, flow_id, triggered_by, operations, overall_success, "
                "time_saved_minutes, cost_saved_yen, created_at",
                count="exact",
            ) \
            .eq("company_id", str(user.company_id)) \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1)

        if flow_id is not None:
            q = q.eq("flow_id", str(flow_id))

        if overall_success is not None:
            q = q.eq("overall_success", overall_success)

        result = q.execute()

        items = []
        for row in result.data or []:
            items.append(ExecutionLogResponse(
                id=row["id"],
                flow_id=row.get("flow_id"),
                triggered_by=row.get("triggered_by"),
                operations=row.get("operations") or {},
                overall_success=row.get("overall_success"),
                time_saved_minutes=row.get("time_saved_minutes"),
                cost_saved_yen=row.get("cost_saved_yen"),
                created_at=row["created_at"],
            ))

        total = result.count or 0
        return ExecutionLogListResponse(
            items=items,
            total=total,
            has_more=(offset + limit) < total,
        )
    except Exception as e:
        logger.error(f"list_execution_logs 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────
# HitL エンドポイント
# ─────────────────────────────────────

@router.get("/execution/pending-approvals", response_model=PendingApprovalsResponse)
async def list_pending_approvals(
    user: JWTClaims = Depends(require_role("admin")),
):
    """承認待ち BPO 実行結果の一覧を返す（admin のみ）。"""
    try:
        db = get_service_client()
        result = (
            db.table("execution_logs")
            .select("id, operations, created_at")
            .eq("company_id", str(user.company_id))
            .eq("approval_status", "pending")
            .order("created_at", desc=True)
            .execute()
        )

        items: list[PendingApprovalItem] = []
        for row in result.data or []:
            ops = row.get("operations") or {}
            pipeline_key = ops.get("pipeline", "")

            steps = ops.get("steps") or []
            confidence = float(steps[-1].get("confidence", 0.0)) if steps else 0.0

            final_output = ops.get("final_output") or {}
            summary = (
                final_output.get("message")
                or final_output.get("summary")
                or f"実行結果 #{row['id'][:8]}"
            )

            pipeline_label = PIPELINE_LABELS.get(pipeline_key, pipeline_key)

            items.append(PendingApprovalItem(
                id=row["id"],
                pipeline_key=pipeline_key,
                pipeline_label=pipeline_label,
                created_at=row["created_at"],
                summary=summary,
                confidence=confidence,
            ))

        return PendingApprovalsResponse(count=len(items), items=items)
    except Exception as e:
        logger.error(f"list_pending_approvals 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execution/{execution_id}/approve")
async def approve_execution(
    execution_id: str,
    body: ApproveRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """BPO 実行結果を承認する。修正内容がある場合は modified_output に保存（admin のみ）。"""
    try:
        db = get_service_client()

        # 存在確認 + company_id チェック（テナント分離）
        row = (
            db.table("execution_logs")
            .select("id, approval_status, company_id")
            .eq("id", execution_id)
            .eq("company_id", str(user.company_id))
            .maybe_single()
            .execute()
        )
        if not row.data:
            raise HTTPException(status_code=404, detail="実行ログが見つかりません")
        if row.data["approval_status"] != "pending":
            raise HTTPException(status_code=400, detail="この実行結果はすでに処理済みです")

        update_data: dict = {
            "approval_status": "modified" if body.modified_output else "approved",
            "approved_by": str(user.sub),
            "approved_at": datetime.utcnow().isoformat(),
        }
        if body.modified_output:
            update_data["modified_output"] = body.modified_output

        db.table("execution_logs").update(update_data).eq("id", execution_id).execute()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"approve_execution 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    await audit_log(
        company_id=str(user.company_id),
        user_id=str(user.sub),
        action="bpo_approved",
        resource_type="execution_log",
        resource_id=execution_id,
        details={"modified": bool(body.modified_output)},
    )

    return {"message": "承認しました", "execution_id": execution_id}


@router.post("/execution/{execution_id}/reject")
async def reject_execution(
    execution_id: str,
    body: RejectRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """BPO 実行結果を却下する（admin のみ）。"""
    try:
        db = get_service_client()

        # 存在確認 + company_id チェック（テナント分離）
        row = (
            db.table("execution_logs")
            .select("id, approval_status, company_id")
            .eq("id", execution_id)
            .eq("company_id", str(user.company_id))
            .maybe_single()
            .execute()
        )
        if not row.data:
            raise HTTPException(status_code=404, detail="実行ログが見つかりません")
        if row.data["approval_status"] != "pending":
            raise HTTPException(status_code=400, detail="この実行結果はすでに処理済みです")

        db.table("execution_logs").update({
            "approval_status": "rejected",
            "approved_by": str(user.sub),
            "approved_at": datetime.utcnow().isoformat(),
            "rejection_reason": body.reason,
        }).eq("id", execution_id).execute()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"reject_execution 失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    await audit_log(
        company_id=str(user.company_id),
        user_id=str(user.sub),
        action="bpo_rejected",
        resource_type="execution_log",
        resource_id=execution_id,
        details={"reason": body.reason},
    )

    return {"message": "却下しました", "execution_id": execution_id}

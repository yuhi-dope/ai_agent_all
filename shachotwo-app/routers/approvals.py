"""Human-in-the-Loop 承認キュー エンドポイント。

承認待ち(pending)の execution_logs を一覧・詳細取得し、
admin が承認 / 却下 / 修正承認できるエンドポイントを提供する。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role, require_min_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────
# パイプライン表示名マッピング（execution.py と同一）
# ─────────────────────────────────────

PIPELINE_LABELS: dict[str, str] = {
    "construction/estimation":     "建設業 見積書",
    "construction/billing":        "建設業 請求書",
    "construction/safety":         "建設業 安全書類",
    "construction/cost_report":    "建設業 原価報告",
    "construction/subcontractor":  "建設業 下請管理",
    "construction/permit":         "建設業 許可申請",
    "construction/photo_organize": "建設業 施工写真整理",
    "manufacturing/quoting":       "製造業 見積書",
    "manufacturing/estimation":    "製造業 見積書",
    "common/expense":              "経費精算",
    "common/payroll":              "給与計算",
    "common/contract":             "契約書",
    "common/attendance":           "勤怠集計",
    "common/vendor":               "取引先マスタ",
    "common/admin_reminder":       "管理リマインダ",
    "restaurant/fl_cost":          "飲食業 FL原価計算",
    "restaurant/shift":            "飲食業 シフト",
    "beauty/recall":               "美容 リコール",
    "staffing/dispatch_contract":  "派遣契約書",
    "architecture/building_permit": "建築確認申請",
    "clinic/medical_receipt":      "医療 レセプト",
    "hotel/revenue_mgmt":          "ホテル 収益管理",
    "pharmacy/dispensing_billing": "薬局 調剤報酬",
    "dental/receipt_check":        "歯科 レセプト点検",
    "dental/receipt":              "歯科 レセプト",
    "realestate/rent_collection":  "不動産 家賃管理",
    "realestate/property":         "不動産 物件管理",
    "professional/deadline_mgmt":  "士業 期限管理",
    "professional/deadline":       "士業 期限管理",
    "nursing/care_billing":        "介護 報酬請求",
    "logistics/dispatch":          "物流 配車",
    "ecommerce/listing":           "EC 商品登録",
    "auto_repair/repair_quoting":  "整備 見積書",
}


def _pipeline_label(pipeline_key: str) -> str:
    return PIPELINE_LABELS.get(pipeline_key, pipeline_key)


def _risk_level(confidence: float | None) -> str:
    """confidence スコアからリスクレベル文字列に変換する。"""
    if confidence is None:
        return "不明"
    if confidence >= 0.8:
        return "低"
    if confidence >= 0.5:
        return "中"
    return "高"


def _extract_confidence(operations: dict) -> float | None:
    """operations.steps から平均 confidence を算出する。"""
    steps = operations.get("steps") or []
    confs = [
        float(s["confidence"])
        for s in steps
        if s.get("confidence") is not None
    ]
    if not confs:
        return None
    return round(sum(confs) / len(confs), 4)


def _extract_summary(operations: dict) -> str:
    """operations から人間が読める要約文字列を生成する。"""
    final_output = operations.get("final_output") or {}
    if isinstance(final_output, dict):
        summary = final_output.get("summary") or final_output.get("title") or ""
        if summary:
            return str(summary)[:200]
    pipeline = operations.get("pipeline", "")
    return f"{_pipeline_label(pipeline)} の実行結果"


# ─────────────────────────────────────
# レスポンスモデル
# ─────────────────────────────────────

class PendingApprovalItem(BaseModel):
    id: str
    pipeline_key: str
    pipeline_label: str
    created_at: datetime
    summary: str
    confidence: Optional[float] = None
    risk_level: str


class PendingApprovalsResponse(BaseModel):
    count: int
    items: list[PendingApprovalItem]


class ApprovalDetailResponse(BaseModel):
    id: str
    pipeline_key: str
    pipeline_label: str
    created_at: datetime
    summary: str
    confidence: Optional[float] = None
    risk_level: str
    approval_status: str
    output_data: dict
    steps: list[dict]
    original_output: Optional[dict] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejection_reason: Optional[str] = None
    inference_reason: Optional[str] = None


class ApproveRequest(BaseModel):
    modified_output: Optional[dict] = None


class RejectRequest(BaseModel):
    reason: str


class ModifyApproveRequest(BaseModel):
    modified_output: dict


class ApprovalActionResponse(BaseModel):
    execution_id: str
    approval_status: str
    message: str


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.get("/approvals/pending", response_model=PendingApprovalsResponse)
async def list_pending_approvals(
    user: JWTClaims = Depends(get_current_user),
):
    """承認待ち(approval_status='pending')の実行ログ一覧を返す。

    admin / editor どちらも参照可。company_id フィルタ必須。
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)

        result = (
            db.table("execution_logs")
            .select(
                "id, operations, overall_success, created_at, "
                "approval_status, approved_by, approved_at, rejection_reason"
            )
            .eq("company_id", company_id)
            .eq("approval_status", "pending")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"list_pending_approvals DB取得失敗: {e}")
        raise HTTPException(status_code=500, detail="承認待ち一覧の取得に失敗しました")

    items: list[PendingApprovalItem] = []
    for row in rows:
        ops: dict = row.get("operations") or {}
        pipeline_key: str = ops.get("pipeline", "")
        confidence = _extract_confidence(ops)
        items.append(PendingApprovalItem(
            id=str(row["id"]),
            pipeline_key=pipeline_key,
            pipeline_label=_pipeline_label(pipeline_key),
            created_at=row["created_at"],
            summary=_extract_summary(ops),
            confidence=confidence,
            risk_level=_risk_level(confidence),
        ))

    return PendingApprovalsResponse(count=len(items), items=items)


@router.get("/approvals/{execution_id}", response_model=ApprovalDetailResponse)
async def get_approval_detail(
    execution_id: str,
    user: JWTClaims = Depends(get_current_user),
):
    """承認詳細を返す。output_data・推論根拠・リスク分類を含む。

    admin / editor どちらも参照可。company_id フィルタ必須。
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)

        result = (
            db.table("execution_logs")
            .select(
                "id, operations, overall_success, created_at, "
                "approval_status, approved_by, approved_at, rejection_reason, "
                "original_output"
            )
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .maybe_single()
            .execute()
        )
        row = result.data
    except Exception as e:
        logger.error(f"get_approval_detail DB取得失敗: {e}")
        raise HTTPException(status_code=500, detail="承認詳細の取得に失敗しました")

    if not row:
        raise HTTPException(status_code=404, detail="指定された実行ログが見つかりません")

    ops: dict = row.get("operations") or {}
    pipeline_key: str = ops.get("pipeline", "")
    confidence = _extract_confidence(ops)
    final_output: dict = ops.get("final_output") or {}
    steps: list[dict] = ops.get("steps") or []

    # 推論根拠（steps の reason / rationale / explanation フィールドを収集）
    reasons: list[str] = []
    for step in steps:
        for key in ("reason", "rationale", "explanation"):
            val = step.get(key) or (step.get("output") or {}).get(key) if isinstance(step.get("output"), dict) else None
            if val and isinstance(val, str):
                reasons.append(val)
    inference_reason = " / ".join(reasons) if reasons else None

    return ApprovalDetailResponse(
        id=str(row["id"]),
        pipeline_key=pipeline_key,
        pipeline_label=_pipeline_label(pipeline_key),
        created_at=row["created_at"],
        summary=_extract_summary(ops),
        confidence=confidence,
        risk_level=_risk_level(confidence),
        approval_status=row.get("approval_status", "pending"),
        output_data=final_output,
        steps=steps,
        original_output=row.get("original_output"),
        approved_by=row.get("approved_by"),
        approved_at=str(row["approved_at"]) if row.get("approved_at") else None,
        rejection_reason=row.get("rejection_reason"),
        inference_reason=inference_reason,
    )


@router.patch("/approvals/{execution_id}/approve", response_model=ApprovalActionResponse)
async def approve_execution(
    execution_id: str,
    body: ApproveRequest,
    user: JWTClaims = Depends(require_min_role("approver")),
):
    """承認する（admin のみ）。

    modified_output が指定された場合は出力内容を上書きして承認する。
    company_id フィルタで所属テナントの execution_log のみ操作可。
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        # 存在確認 + テナント確認
        check = (
            db.table("execution_logs")
            .select("id, approval_status, operations")
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=404, detail="指定された実行ログが見つかりません")

        current_status = check.data.get("approval_status")
        if current_status not in ("pending", None):
            raise HTTPException(
                status_code=409,
                detail=f"すでに処理済みです（現在のステータス: {current_status}）",
            )

        # 自己承認禁止チェック（職務分離）
        requested_by = (check.data.get("operations") or {}).get("requested_by") or check.data.get("requested_by")
        if requested_by and str(requested_by) == user.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="自分が起票した申請を自分で承認することはできません（職務分離）",
            )

        update_payload: dict = {
            "approval_status": "approved",
            "approved_by": user.sub,
            "approved_at": now_iso,
        }

        # 修正承認の場合は operations.final_output を更新する
        if body.modified_output is not None:
            ops: dict = check.data.get("operations") or {}
            ops["final_output"] = body.modified_output
            update_payload["operations"] = ops

        (
            db.table("execution_logs")
            .update(update_payload)
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .execute()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"approve_execution 失敗: {e}")
        raise HTTPException(status_code=500, detail="承認処理に失敗しました")

    return ApprovalActionResponse(
        execution_id=execution_id,
        approval_status="approved",
        message="承認しました",
    )


@router.patch("/approvals/{execution_id}/reject", response_model=ApprovalActionResponse)
async def reject_execution(
    execution_id: str,
    body: RejectRequest,
    user: JWTClaims = Depends(require_min_role("approver")),
):
    """却下する（admin のみ）。rejection_reason は必須。

    company_id フィルタで所属テナントの execution_log のみ操作可。
    """
    if not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="却下理由を入力してください",
        )

    try:
        db = get_service_client()
        company_id = str(user.company_id)

        check = (
            db.table("execution_logs")
            .select("id, approval_status, operations, requested_by")
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=404, detail="指定された実行ログが見つかりません")

        current_status = check.data.get("approval_status")
        if current_status not in ("pending", None):
            raise HTTPException(
                status_code=409,
                detail=f"すでに処理済みです（現在のステータス: {current_status}）",
            )

        # 自己承認禁止チェック（職務分離）
        requested_by = (check.data.get("operations") or {}).get("requested_by") or check.data.get("requested_by")
        if requested_by and str(requested_by) == user.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="自分が起票した申請を自分で却下することはできません（職務分離）",
            )

        (
            db.table("execution_logs")
            .update({
                "approval_status": "rejected",
                "approved_by": user.sub,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "rejection_reason": body.reason.strip(),
            })
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .execute()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"reject_execution 失敗: {e}")
        raise HTTPException(status_code=500, detail="却下処理に失敗しました")

    return ApprovalActionResponse(
        execution_id=execution_id,
        approval_status="rejected",
        message="却下しました",
    )


@router.patch("/approvals/{execution_id}/modify", response_model=ApprovalActionResponse)
async def modify_approve_execution(
    execution_id: str,
    body: ModifyApproveRequest,
    user: JWTClaims = Depends(require_min_role("approver")),
):
    """出力内容を修正して承認する（admin のみ）。

    modified_output で operations.final_output を上書きし、approval_status を approved に変更する。
    company_id フィルタで所属テナントの execution_log のみ操作可。
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        check = (
            db.table("execution_logs")
            .select("id, approval_status, operations, requested_by")
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .maybe_single()
            .execute()
        )
        if not check.data:
            raise HTTPException(status_code=404, detail="指定された実行ログが見つかりません")

        current_status = check.data.get("approval_status")
        if current_status not in ("pending", None):
            raise HTTPException(
                status_code=409,
                detail=f"すでに処理済みです（現在のステータス: {current_status}）",
            )

        # 自己承認禁止チェック（職務分離）
        ops: dict = check.data.get("operations") or {}
        requested_by = ops.get("requested_by") or check.data.get("requested_by")
        if requested_by and str(requested_by) == user.sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="自分が起票した申請を自分で承認することはできません（職務分離）",
            )

        ops["final_output"] = body.modified_output

        (
            db.table("execution_logs")
            .update({
                "approval_status": "approved",
                "approved_by": user.sub,
                "approved_at": now_iso,
                "operations": ops,
            })
            .eq("company_id", company_id)
            .eq("id", execution_id)
            .execute()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"modify_approve_execution 失敗: {e}")
        raise HTTPException(status_code=500, detail="修正承認処理に失敗しました")

    return ApprovalActionResponse(
        execution_id=execution_id,
        approval_status="approved",
        message="修正して承認しました",
    )

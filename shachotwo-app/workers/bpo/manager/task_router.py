"""BPO Manager — TaskRouter。発見タスクを適切なパイプラインにルーティングする。"""
import asyncio
import importlib
import logging
from typing import Any

from workers.bpo.manager.models import BPOTask, ExecutionLevel, PipelineResult

logger = logging.getLogger(__name__)

# パイプラインレジストリ（pipeline名 → 実行関数のモジュールパス）
PIPELINE_REGISTRY: dict[str, str] = {
    # 建設業
    "construction/estimation":  "workers.bpo.construction.pipelines.estimation_pipeline.run_estimation_pipeline",
    "construction/billing":     "workers.bpo.construction.pipelines.billing_pipeline.run_billing_pipeline",
    "construction/safety_docs": "workers.bpo.construction.pipelines.safety_docs_pipeline.run_safety_docs_pipeline",
    "construction/cost_report":    "workers.bpo.construction.pipelines.cost_report_pipeline.run_cost_report_pipeline",
    "construction/photo_organize": "workers.bpo.construction.pipelines.photo_organize_pipeline.run_photo_organize_pipeline",
    "construction/subcontractor":  "workers.bpo.construction.pipelines.subcontractor_pipeline.run_subcontractor_pipeline",
    "construction/permit":         "workers.bpo.construction.pipelines.permit_pipeline.run_permit_pipeline",
    "construction/construction_plan": "workers.bpo.construction.pipelines.construction_plan_pipeline.run_construction_plan_pipeline",
    # 製造業（Phase 2+）
    "manufacturing/quoting":         "workers.bpo.manufacturing.pipelines.quoting_pipeline.run_quoting_pipeline",
    # 歯科
    "dental/receipt_check":     "workers.bpo.dental.pipelines.receipt_check_pipeline.run_receipt_check_pipeline",
    # 共通BPO
    "common/expense":           "workers.bpo.common.pipelines.expense_pipeline.run_expense_pipeline",
    "common/payroll":           "workers.bpo.common.pipelines.payroll_pipeline.run_payroll_pipeline",
    # 飲食業
    "restaurant/fl_cost":       "workers.bpo.restaurant.pipelines.fl_cost_pipeline.run_fl_cost_pipeline",
    "restaurant/shift":         "workers.bpo.restaurant.pipelines.shift_pipeline.run_shift_pipeline",
    # 共通BPO
    "common/attendance":        "workers.bpo.common.pipelines.attendance_pipeline.run_attendance_pipeline",
    "common/contract":          "workers.bpo.common.pipelines.contract_pipeline.run_contract_pipeline",
    "common/admin_reminder":    "workers.bpo.common.pipelines.admin_reminder_pipeline.run_admin_reminder_pipeline",
    "common/vendor":            "workers.bpo.common.pipelines.vendor_pipeline.run_vendor_pipeline",
    # ★ 不動産管理
    "realestate/rent_collection": "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_rent_collection_pipeline",
    # ★ 士業事務所
    "professional/deadline_mgmt": "workers.bpo.professional.pipelines.deadline_mgmt_pipeline.run_deadline_mgmt_pipeline",
    # A群: 介護・福祉
    "nursing/care_billing":     "workers.bpo.nursing.pipelines.care_billing_pipeline.run_care_billing_pipeline",
    # A群: 物流・運送
    "logistics/dispatch":       "workers.bpo.logistics.pipelines.dispatch_pipeline.run_dispatch_pipeline",
    # B群: 医療クリニック
    "clinic/medical_receipt":   "workers.bpo.clinic.pipelines.medical_receipt_pipeline.run_medical_receipt_pipeline",
    # B群: 調剤薬局
    "pharmacy/dispensing_billing": "workers.bpo.pharmacy.pipelines.dispensing_billing_pipeline.run_dispensing_billing_pipeline",
    # B群: 美容・エステ
    "beauty/booking_recall":    "workers.bpo.beauty.pipelines.recall_pipeline.run_recall_pipeline",
    # B群: 自動車整備
    "auto_repair/repair_quoting": "workers.bpo.auto_repair.pipelines.repair_quoting_pipeline.run_repair_quoting_pipeline",
    # B群: ホテル・旅館
    "hotel/revenue_mgmt":       "workers.bpo.hotel.pipelines.revenue_mgmt_pipeline.run_revenue_mgmt_pipeline",
    # B群: EC・小売
    "ecommerce/product_listing": "workers.bpo.ecommerce.pipelines.listing_pipeline.run_listing_pipeline",
    # B群: 人材派遣
    "staffing/dispatch_contract": "workers.bpo.staffing.pipelines.dispatch_contract_pipeline.run_dispatch_contract_pipeline",
    # B群: 建築設計
    "architecture/building_permit": "workers.bpo.architecture.pipelines.building_permit_pipeline.run_building_permit_pipeline",
}

# テナント別同時実行制御（最大3パイプライン同時）
MAX_CONCURRENT_PIPELINES_PER_TENANT = 3
_tenant_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_tenant_semaphore(company_id: str) -> asyncio.Semaphore:
    """テナント別のSemaphoreを取得（なければ作成）"""
    if company_id not in _tenant_semaphores:
        _tenant_semaphores[company_id] = asyncio.Semaphore(MAX_CONCURRENT_PIPELINES_PER_TENANT)
    return _tenant_semaphores[company_id]


def determine_approval_required(task: BPOTask, trust_score: float = 0.0) -> bool:
    """
    実行レベルと信頼スコアから承認要否を判定する。

    Level 0-1: 承認不要（READ ONLY）
    Level 2: 推奨（estimated_impact > 0.7 なら必須）
    Level 3: 必須
    Level 4: trust_score >= 0.95 かつ estimated_impact < 0.5 なら不要
    """
    level = task.execution_level

    if level <= ExecutionLevel.DATA_COLLECT:
        return False

    if level == ExecutionLevel.DRAFT_CREATE:
        return task.estimated_impact > 0.7

    if level == ExecutionLevel.APPROVAL_GATED:
        return True

    if level == ExecutionLevel.AUTONOMOUS:
        return not (trust_score >= 0.95 and task.estimated_impact < 0.5)

    return True


async def _save_approval_pending(task: BPOTask, draft_result: dict[str, Any]) -> None:
    """承認待ち状態をproactive_proposalsに保存する。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        db.table("proactive_proposals").insert({
            "company_id": task.company_id,
            "proposal_type": "bpo_task",
            "title": f"BPO承認待ち: {task.pipeline}",
            "description": f"パイプライン {task.pipeline} の実行承認が必要です",
            "status": "pending",
            "impact_score": task.estimated_impact,
            "metadata": {
                "pipeline": task.pipeline,
                "trigger_type": task.trigger_type,
                "execution_level": task.execution_level,
                "input_data": task.input_data,
                "draft_result": draft_result,
                "knowledge_item_ids": task.knowledge_item_ids,
            },
        }).execute()
    except Exception as e:
        logger.error(f"承認待ち保存失敗: {e}")


async def route_and_execute(
    task: BPOTask,
    trust_score: float = 0.0,
    force_dry_run: bool = False,
) -> PipelineResult:
    """
    BPOTaskを受け取り、適切なパイプラインを呼び出す。

    1. パイプラインが登録済みか確認
    2. 承認要否判定
    3. 承認不要 → パイプライン実行
    4. 承認必要 → proactive_proposalsにドラフト保存して返す
    """
    pipeline_key = task.pipeline

    if pipeline_key not in PIPELINE_REGISTRY:
        logger.error(f"未登録パイプライン: {pipeline_key}")
        return PipelineResult(
            success=False,
            pipeline=pipeline_key,
            failed_step="task_router",
            final_output={"error": f"パイプライン '{pipeline_key}' は未登録です。PIPELINE_REGISTRYに追加してください。"},
        )

    approval_required = determine_approval_required(task, trust_score)

    if approval_required and not force_dry_run:
        logger.info(f"承認待ち: pipeline={pipeline_key} impact={task.estimated_impact}")
        await _save_approval_pending(task, {})
        return PipelineResult(
            success=True,
            pipeline=pipeline_key,
            approval_pending=True,
            final_output={"message": "承認待ち。proactive_proposalsを確認してください。"},
        )

    # パイプライン関数を動的にロード
    module_path, func_name = PIPELINE_REGISTRY[pipeline_key].rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        pipeline_func = getattr(module, func_name)
    except (ImportError, AttributeError) as e:
        logger.error(f"パイプラインロード失敗: {pipeline_key} — {e}")
        return PipelineResult(
            success=False,
            pipeline=pipeline_key,
            failed_step="task_router",
            final_output={"error": f"パイプライン未実装: {e}"},
        )

    # 同時実行制御
    sem = _get_tenant_semaphore(task.company_id)
    if sem.locked() and sem._value == 0:  # type: ignore[attr-defined]
        logger.warning(f"同時実行上限到達: company={task.company_id}")
        return PipelineResult(
            success=False,
            pipeline=pipeline_key,
            failed_step="concurrency_limit",
            final_output={"error": f"同時実行上限（{MAX_CONCURRENT_PIPELINES_PER_TENANT}）に達しています。しばらく待ってから再試行してください。"},
        )

    async with sem:
        # パイプライン実行（5分タイムアウト）
        PIPELINE_TIMEOUT_SECONDS = 300
        try:
            raw = await asyncio.wait_for(
                pipeline_func(
                    company_id=task.company_id,
                    input_data=task.input_data,
                    **task.context,
                ),
                timeout=PIPELINE_TIMEOUT_SECONDS,
            )
            # EstimationPipelineResult → PipelineResult に変換
            if hasattr(raw, "success"):
                return PipelineResult(
                    success=raw.success,
                    pipeline=pipeline_key,
                    steps=[{"step": s.step_name, "confidence": s.confidence, "cost_yen": s.cost_yen}
                           for s in getattr(raw, "steps", [])],
                    final_output=getattr(raw, "final_output", {}),
                    total_cost_yen=getattr(raw, "total_cost_yen", 0.0),
                    total_duration_ms=getattr(raw, "total_duration_ms", 0),
                    failed_step=getattr(raw, "failed_step", None),
                )
            return PipelineResult(success=True, pipeline=pipeline_key, final_output=raw or {})
        except asyncio.TimeoutError:
            logger.error(f"パイプラインタイムアウト: {pipeline_key} ({PIPELINE_TIMEOUT_SECONDS}秒)")
            return PipelineResult(
                success=False,
                pipeline=pipeline_key,
                failed_step="timeout",
                final_output={"error": f"パイプラインが{PIPELINE_TIMEOUT_SECONDS}秒以内に完了しませんでした。"},
            )
        except Exception as e:
            logger.error(f"パイプライン実行エラー: {pipeline_key} — {e}")
            return PipelineResult(
                success=False,
                pipeline=pipeline_key,
                failed_step="pipeline_execution",
                final_output={"error": str(e)},
            )

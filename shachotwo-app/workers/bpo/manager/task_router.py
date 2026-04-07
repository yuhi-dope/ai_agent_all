"""BPO Manager — TaskRouter。発見タスクを適切なパイプラインにルーティングする。"""
import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from workers.bpo.manager.models import BPOTask, ExecutionLevel, PipelineResult
from workers.bpo.manager.notifier import notify_pipeline_event
from workers.bpo.engine.genome_registry import get_loaded_genome_registry

logger = logging.getLogger(__name__)


# ─── Circuit Breaker ──────────────────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"        # 正常稼働
    OPEN = "open"            # トリップ中（実行スキップ）
    HALF_OPEN = "half_open"  # 復帰試行中


@dataclass
class CircuitBreakerState:
    consecutive_failures: int = 0
    state: CircuitState = CircuitState.CLOSED
    tripped_at: Optional[datetime] = None


# インメモリ Circuit Breaker ストア（pipeline_key → CircuitBreakerState）
_circuit_breakers: dict[str, CircuitBreakerState] = {}

# Circuit Breaker 設定値
CB_FAILURE_THRESHOLD = 5          # 連続失敗回数しきい値
CB_RECOVERY_SECONDS = 30 * 60    # half-open に移行するまでの秒数（30分）


def _get_circuit_breaker(pipeline_key: str) -> CircuitBreakerState:
    """Circuit Breaker の状態を取得（なければ初期化）。"""
    if pipeline_key not in _circuit_breakers:
        _circuit_breakers[pipeline_key] = CircuitBreakerState()
    return _circuit_breakers[pipeline_key]


def _is_circuit_open(pipeline_key: str) -> bool:
    """
    パイプラインの Circuit Breaker が「実行ブロック」状態かどうかを返す。

    - CLOSED → False（正常実行）
    - OPEN かつ30分未経過 → True（スキップ）
    - OPEN かつ30分経過 → False（half-open として試行を許可し状態を更新）
    - HALF_OPEN → False（試行を継続）
    """
    cb = _get_circuit_breaker(pipeline_key)

    if cb.state == CircuitState.CLOSED:
        return False

    if cb.state == CircuitState.OPEN:
        now = datetime.now(timezone.utc)
        elapsed = (now - cb.tripped_at).total_seconds() if cb.tripped_at else CB_RECOVERY_SECONDS + 1
        if elapsed >= CB_RECOVERY_SECONDS:
            # 30分経過 → half-open に移行して1回試行を許可
            cb.state = CircuitState.HALF_OPEN
            logger.info(f"Circuit Breaker half-open: {pipeline_key}")
            return False
        return True  # まだ30分経っていない

    # HALF_OPEN → 試行を許可
    return False


async def _record_success(pipeline_key: str) -> None:
    """パイプライン成功時に Circuit Breaker をリセットする。"""
    cb = _get_circuit_breaker(pipeline_key)
    if cb.state != CircuitState.CLOSED or cb.consecutive_failures > 0:
        logger.info(f"Circuit Breaker reset (closed): {pipeline_key}")
    cb.consecutive_failures = 0
    cb.state = CircuitState.CLOSED
    cb.tripped_at = None


async def _record_failure(pipeline_key: str, company_id: str) -> None:
    """
    パイプライン失敗時に Circuit Breaker を更新する。

    - HALF_OPEN での失敗 → 即トリップ（再OPEN）
    - CLOSED での連続失敗がしきい値到達 → トリップ
    """
    cb = _get_circuit_breaker(pipeline_key)
    cb.consecutive_failures += 1

    should_trip = (
        cb.state == CircuitState.HALF_OPEN
        or cb.consecutive_failures >= CB_FAILURE_THRESHOLD
    )

    if should_trip and cb.state != CircuitState.OPEN:
        cb.state = CircuitState.OPEN
        cb.tripped_at = datetime.now(timezone.utc)
        logger.warning(
            f"Circuit Breaker tripped: {pipeline_key} "
            f"(consecutive_failures={cb.consecutive_failures})"
        )
        try:
            await notify_pipeline_event(
                company_id=company_id,
                pipeline=pipeline_key,
                event_type="circuit_breaker",
                details={
                    "consecutive_failures": cb.consecutive_failures,
                    "tripped_at": cb.tripped_at.isoformat(),
                    "message": (
                        f"パイプライン '{pipeline_key}' が{cb.consecutive_failures}回連続失敗したため"
                        " Circuit Breaker がトリップしました。30分後に自動復帰試行します。"
                    ),
                },
            )
        except Exception as notify_err:
            logger.error(f"Circuit Breaker trip通知失敗: {notify_err}")


# パイプラインレジストリ（pipeline名 → 実行関数のモジュールパス）
PIPELINE_REGISTRY: dict[str, str] = {
    # ── 建設業 ──────────────────────────────────────
    "construction/estimation":  "workers.bpo.construction.pipelines.estimation_pipeline.run_estimation_pipeline",
    "construction/billing":     "workers.bpo.construction.pipelines.billing_pipeline.run_billing_pipeline",
    "construction/safety_docs": "workers.bpo.construction.pipelines.safety_docs_pipeline.run_safety_docs_pipeline",
    "construction/cost_report":    "workers.bpo.construction.pipelines.cost_report_pipeline.run_cost_report_pipeline",
    "construction/photo_organize": "workers.bpo.construction.pipelines.photo_organize_pipeline.run_photo_organize_pipeline",
    "construction/subcontractor":  "workers.bpo.construction.pipelines.subcontractor_pipeline.run_subcontractor_pipeline",
    "construction/permit":         "workers.bpo.construction.pipelines.permit_pipeline.run_permit_pipeline",
    "construction/construction_plan": "workers.bpo.construction.pipelines.construction_plan_pipeline.run_construction_plan_pipeline",
    # ── 製造業 ──────────────────────────────────────
    "manufacturing/quoting":         "workers.bpo.manufacturing.pipelines.quoting_pipeline.run_quoting_pipeline",
    # ── 共通BPO ─────────────────────────────────────
    "common/expense":           "workers.bpo.common.pipelines.expense_pipeline.run_expense_pipeline",
    "common/payroll":           "workers.bpo.common.pipelines.payroll_pipeline.run_payroll_pipeline",
    "common/attendance":        "workers.bpo.common.pipelines.attendance_pipeline.run_attendance_pipeline",
    "common/contract":          "workers.bpo.common.pipelines.contract_pipeline.run_contract_pipeline",
    "common/admin_reminder":    "workers.bpo.common.pipelines.admin_reminder_pipeline.run_admin_reminder_pipeline",
    "common/vendor":            "workers.bpo.common.pipelines.vendor_pipeline.run_vendor_pipeline",
    # ── 医療クリニック ──────────────────────────────
    "clinic/medical_receipt":   "workers.bpo.clinic.pipelines.medical_receipt_pipeline.run_medical_receipt_pipeline",
    # ── 介護・福祉 ──────────────────────────────────
    "nursing/care_billing":     "workers.bpo.nursing.pipelines.care_billing_pipeline.run_care_billing_pipeline",
    # ── 不動産管理 ──────────────────────────────────
    "realestate/rent_collection": "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_rent_collection_pipeline",
    # ── 物流・運送 ──────────────────────────────────
    "logistics/dispatch":       "workers.bpo.logistics.pipelines.dispatch_pipeline.run_dispatch_pipeline",
    # ── 卸売業 ─────────────────────────────────────
    "wholesale/order_processing":     "workers.bpo.wholesale.pipelines.order_processing_pipeline.run_order_processing_pipeline",
    "wholesale/inventory_management": "workers.bpo.wholesale.pipelines.inventory_management_pipeline.run_inventory_management_pipeline",
    "wholesale/accounts_receivable":  "workers.bpo.wholesale.pipelines.accounts_receivable_pipeline.run_accounts_receivable_pipeline",
    "wholesale/accounts_payable":     "workers.bpo.wholesale.pipelines.accounts_payable_pipeline.run_accounts_payable_pipeline",
    "wholesale/shipping":             "workers.bpo.wholesale.pipelines.shipping_pipeline.run_shipping_pipeline",
    "wholesale/sales_intelligence":   "workers.bpo.wholesale.pipelines.sales_intelligence_pipeline.run_sales_intelligence_pipeline",
    # ── セールス・CS パイプライン（BPO Manager 自動トリガー対象）──────────
    # marketing: アウトリーチ — 企業リサーチ & アウトリーチ 400 件/日（毎日 08:00）
    "sales/outreach":               "workers.bpo.sales.marketing.outreach_pipeline.run_outreach_pipeline",
    # sfa: リード資格審査 — lead_created イベント時
    "sales/lead_qualification":     "workers.bpo.sales.sfa.lead_qualification_pipeline.run_lead_qualification_pipeline",
    # sfa: 提案書自動生成 — lead_score >= 70 時
    "sales/proposal_generation":    "workers.bpo.sales.sfa.proposal_generation_pipeline.run_proposal_generation_pipeline",
    # sfa: 見積・契約書作成 — proposal_accepted 時
    "sales/quotation_contract":     "workers.bpo.sales.sfa.quotation_contract_pipeline.run_quotation_contract_pipeline",
    # sfa: 電子同意フロー — quotation_contract 後に同意取得
    "sales/consent_flow":           "workers.bpo.sales.sfa.consent_flow.run_consent_flow_pipeline",
    # crm: 顧客ライフサイクル — onboarding（contract_signed）/ health_check（毎日 09:00）
    "sales/customer_lifecycle":     "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_customer_lifecycle_pipeline",
    # cs: サポート自動応答 — ticket_created / SLA 違反チェック（毎日 10:00）
    "sales/support_auto_response":  "workers.bpo.sales.cs.support_auto_response_pipeline.run_support_auto_response_pipeline",
    # learning: 受注/失注フィードバック — opportunity_won / opportunity_lost / アウトリーチ PDCA（毎週月曜）
    "sales/win_loss_feedback":      "workers.bpo.sales.learning.win_loss_feedback_pipeline.run_win_loss_feedback_pipeline",
    # cs: アップセル提案 — health_score_high イベント時
    "sales/upsell_briefing":        "workers.bpo.sales.cs.upsell_briefing_pipeline.run_upsell_briefing_pipeline",
    # cs: 解約フロー — cancellation_requested イベント時（承認必須）
    "sales/cancellation":           "workers.bpo.sales.cs.cancellation_pipeline.run_cancellation_pipeline",
    # crm: 収益・要望レポート — 毎月1日（MRR/チャーン）/ 毎月15日（要望ランキング）
    "sales/revenue_report":         "workers.bpo.sales.crm.revenue_request_pipeline.run_revenue_request_pipeline",
    # learning: CS 品質月次レビュー — 毎月末
    "sales/cs_feedback":            "workers.bpo.sales.learning.cs_feedback_pipeline.run_cs_feedback_pipeline",
    # ── ドメイン別エイリアス（sales/ プレフィックスの別名）────────────────
    # 以下は sales/* キーと同一パイプライン関数を指す。ドメイン分割表記での呼び出しを許容する。
    "marketing/outreach":           "workers.bpo.sales.marketing.outreach_pipeline.run_outreach_pipeline",
    "sfa/lead_qualification":       "workers.bpo.sales.sfa.lead_qualification_pipeline.run_lead_qualification_pipeline",
    "sfa/proposal_generation":      "workers.bpo.sales.sfa.proposal_generation_pipeline.run_proposal_generation_pipeline",
    "sfa/quotation_contract":       "workers.bpo.sales.sfa.quotation_contract_pipeline.run_quotation_contract_pipeline",
    "sfa/consent_flow":             "workers.bpo.sales.sfa.consent_flow.run_consent_flow_pipeline",
    "crm/customer_lifecycle":       "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_customer_lifecycle_pipeline",
    "crm/revenue_request":          "workers.bpo.sales.crm.revenue_request_pipeline.run_revenue_request_pipeline",
    "cs/support_auto_response":     "workers.bpo.sales.cs.support_auto_response_pipeline.run_support_auto_response_pipeline",
    "cs/upsell_briefing":           "workers.bpo.sales.cs.upsell_briefing_pipeline.run_upsell_briefing_pipeline",
    "cs/cancellation":              "workers.bpo.sales.cs.cancellation_pipeline.run_cancellation_pipeline",
    "learning/win_loss_feedback":   "workers.bpo.sales.learning.win_loss_feedback_pipeline.run_win_loss_feedback_pipeline",
    "learning/cs_feedback":         "workers.bpo.sales.learning.cs_feedback_pipeline.run_cs_feedback_pipeline",
    # ── バックオフィス（経理）────────────────────────────
    "backoffice/invoice_issue":     "workers.bpo.common.pipelines.invoice_issue_pipeline.run_invoice_issue_pipeline",
    "backoffice/ar_management":     "workers.bpo.common.pipelines.ar_management_pipeline.run_ar_management_pipeline",
    "backoffice/ap_management":     "workers.bpo.common.pipelines.ap_management_pipeline.run_ap_management_pipeline",
    "backoffice/bank_reconciliation": "workers.bpo.common.pipelines.bank_reconciliation_pipeline.run_bank_reconciliation_pipeline",
    "backoffice/journal_entry":     "workers.bpo.common.pipelines.journal_entry_pipeline.run_journal_entry_pipeline",
    "backoffice/monthly_close":     "workers.bpo.common.pipelines.monthly_close_pipeline.run_monthly_close_pipeline",
    "backoffice/tax_filing":        "workers.bpo.common.pipelines.tax_filing_pipeline.run_tax_filing_pipeline",
    # ── バックオフィス（労務）────────────────────────────
    "backoffice/social_insurance":  "workers.bpo.common.pipelines.social_insurance_pipeline.run_social_insurance_pipeline",
    "backoffice/year_end_adjustment": "workers.bpo.common.pipelines.year_end_adjustment_pipeline.run_year_end_adjustment_pipeline",
    "backoffice/labor_compliance":  "workers.bpo.common.pipelines.labor_compliance_pipeline.run_labor_compliance_pipeline",
    # ── バックオフィス（人事）────────────────────────────
    "backoffice/recruitment":       "workers.bpo.common.pipelines.recruitment_pipeline.run_recruitment_pipeline",
    "backoffice/employee_onboarding": "workers.bpo.common.pipelines.employee_onboarding_pipeline.run_employee_onboarding_pipeline",
    "backoffice/employee_offboarding": "workers.bpo.common.pipelines.employee_offboarding_pipeline.run_employee_offboarding_pipeline",
    # ── バックオフィス（調達・法務・IT）─────────────────
    "backoffice/purchase_order":    "workers.bpo.common.pipelines.purchase_order_pipeline.run_purchase_order_pipeline",
    "backoffice/compliance_check":  "workers.bpo.common.pipelines.compliance_check_pipeline.run_compliance_check_pipeline",
    "backoffice/antisocial_screening": "workers.bpo.common.pipelines.antisocial_screening_pipeline.run_antisocial_screening_pipeline",
    "backoffice/account_lifecycle": "workers.bpo.common.pipelines.account_lifecycle_pipeline.run_account_lifecycle_pipeline",
    # ── 内部システムパイプライン ────────────────────────
    "internal/accuracy_check":        "brain.inference.accuracy_monitor.run_accuracy_check_pipeline",
    "internal/gws_pending_sync":       "workers.gws.sync_engine_runner.run_pending_syncs_pipeline",
    "internal/improvement_cycle":     "brain.inference.improvement_cycle.run_auto_improvement_cycle",
    "internal/data_purge":            "workers.bpo.common.pipelines.data_purge_pipeline.run_data_purge_pipeline",
    # ── 凍結業種（パートナー主導で復活）──────────────
    # "dental/receipt_check":          "workers.bpo.dental.pipelines.receipt_check_pipeline.run_receipt_check_pipeline",
    # "restaurant/fl_cost":            "workers.bpo.restaurant.pipelines.fl_cost_pipeline.run_fl_cost_pipeline",
    # "restaurant/shift":              "workers.bpo.restaurant.pipelines.shift_pipeline.run_shift_pipeline",
    # "professional/deadline_mgmt":    "workers.bpo.professional.pipelines.deadline_mgmt_pipeline.run_deadline_mgmt_pipeline",
    # "pharmacy/dispensing_billing":   "workers.bpo.pharmacy.pipelines.dispensing_billing_pipeline.run_dispensing_billing_pipeline",
    # "beauty/booking_recall":         "workers.bpo.beauty.pipelines.recall_pipeline.run_recall_pipeline",
    # "auto_repair/repair_quoting":    "workers.bpo.auto_repair.pipelines.repair_quoting_pipeline.run_repair_quoting_pipeline",
    # "hotel/revenue_mgmt":            "workers.bpo.hotel.pipelines.revenue_mgmt_pipeline.run_revenue_mgmt_pipeline",
    # "ecommerce/product_listing":     "workers.bpo.ecommerce.pipelines.listing_pipeline.run_listing_pipeline",
    # "staffing/dispatch_contract":    "workers.bpo.staffing.pipelines.dispatch_contract_pipeline.run_dispatch_contract_pipeline",
    # "architecture/building_permit":  "workers.bpo.architecture.pipelines.building_permit_pipeline.run_building_permit_pipeline",
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


async def _get_effective_registry() -> dict[str, str]:
    """静的 PIPELINE_REGISTRY にゲノム由来のパイプラインをマージして返す。

    静的定義が常に優先される（後方互換保証）。
    GenomeRegistry のロードに失敗した場合は静的レジストリのみを返す。
    """
    try:
        genome = await get_loaded_genome_registry()
        return genome.merge_with_static(PIPELINE_REGISTRY)
    except Exception as e:
        logger.warning(f"GenomeRegistry マージ失敗。静的レジストリのみ使用: {e}")
        return dict(PIPELINE_REGISTRY)


async def route_and_execute(
    task: BPOTask,
    trust_score: float = 0.0,
    force_dry_run: bool = False,
) -> PipelineResult:
    """
    BPOTaskを受け取り、適切なパイプラインを呼び出す。

    1. Circuit Breaker チェック（tripped状態なら即スキップ）
    2. パイプラインが登録済みか確認（静的 + ゲノム由来のマージ済みレジストリ）
    3. 承認要否判定
    4. 承認不要 → パイプライン実行
    5. 承認必要 → proactive_proposalsにドラフト保存して返す
    """
    pipeline_key = task.pipeline

    # ── Circuit Breaker チェック ──────────────────────────────────────────────
    # internal/ プレフィックスのパイプライン（精度監視・GWS同期等）は
    # Circuit Breaker の対象外とする（システム自体の自己診断を止めないため）
    if not pipeline_key.startswith("internal/") and _is_circuit_open(pipeline_key):
        cb = _get_circuit_breaker(pipeline_key)
        logger.warning(
            f"Circuit Breaker OPEN: {pipeline_key} をスキップ "
            f"(tripped_at={cb.tripped_at}, consecutive_failures={cb.consecutive_failures})"
        )
        return PipelineResult(
            success=False,
            pipeline=pipeline_key,
            failed_step="circuit_breaker",
            final_output={
                "error": (
                    f"パイプライン '{pipeline_key}' は Circuit Breaker がトリップ中のため実行をスキップしました。"
                    f" tripped_at={cb.tripped_at.isoformat() if cb.tripped_at else 'unknown'}"
                )
            },
        )

    effective_registry = await _get_effective_registry()

    if pipeline_key not in effective_registry:
        logger.error(f"未登録パイプライン: {pipeline_key}")
        return PipelineResult(
            success=False,
            pipeline=pipeline_key,
            failed_step="task_router",
            final_output={"error": f"パイプライン '{pipeline_key}' は未登録です。PIPELINE_REGISTRYに追加してください。"},
        )

    # ── HITL閾値チェック: min_confidence_for_auto が設定されていれば自動承認を許可 ──
    hitl_auto_approved = False
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        hitl = db.table("bpo_hitl_requirements").select(
            "requires_approval, min_confidence_for_auto"
        ).eq("pipeline_key", pipeline_key).limit(1).execute()
        if hitl.data:
            row = hitl.data[0]
            min_conf = row.get("min_confidence_for_auto")
            if min_conf is not None and trust_score >= float(min_conf):
                hitl_auto_approved = True
                logger.info(f"HITL自動承認: {pipeline_key} trust={trust_score:.2f} >= min_conf={min_conf}")
    except Exception as e:
        logger.debug(f"HITL閾値チェックスキップ: {e}")

    approval_required = determine_approval_required(task, trust_score)

    # HITL閾値で自動承認された場合はスキップ
    if hitl_auto_approved:
        approval_required = False
        logger.info(f"HITL閾値クリア → 承認スキップ: {pipeline_key}")

    if approval_required and not force_dry_run:
        logger.info(f"承認待ち: pipeline={pipeline_key} impact={task.estimated_impact}")
        await _save_approval_pending(task, {})
        await notify_pipeline_event(
            company_id=task.company_id,
            pipeline=pipeline_key,
            event_type="approval_needed",
            details={"approval_url": f"/bpo/approvals?pipeline={pipeline_key}"},
        )
        return PipelineResult(
            success=True,
            pipeline=pipeline_key,
            approval_pending=True,
            final_output={"message": "承認待ち。proactive_proposalsを確認してください。"},
        )

    # パイプライン関数を動的にロード（マージ済みレジストリから取得）
    module_path, func_name = effective_registry[pipeline_key].rsplit(".", 1)
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
                result = PipelineResult(
                    success=raw.success,
                    pipeline=pipeline_key,
                    steps=[{"step": s.step_name, "confidence": s.confidence, "cost_yen": s.cost_yen}
                           for s in getattr(raw, "steps", [])],
                    final_output=getattr(raw, "final_output", {}),
                    total_cost_yen=getattr(raw, "total_cost_yen", 0.0),
                    total_duration_ms=getattr(raw, "total_duration_ms", 0),
                    failed_step=getattr(raw, "failed_step", None),
                )
                await notify_pipeline_event(
                    company_id=task.company_id,
                    pipeline=pipeline_key,
                    event_type="completed" if result.success else "error",
                    details={"cost_yen": result.total_cost_yen, "error": result.final_output.get("error")},
                )
                # ── Circuit Breaker 更新 ──────────────────────────────────
                if result.success:
                    await _record_success(pipeline_key)
                else:
                    await _record_failure(pipeline_key, task.company_id)
                # パイプライン完了後に条件評価器を呼び出して連鎖トリガーを評価
                try:
                    from workers.bpo.manager.condition_evaluator import evaluate_knowledge_triggers
                    chain_tasks = await evaluate_knowledge_triggers(task.company_id)
                    for ct in chain_tasks:
                        asyncio.create_task(route_and_execute(ct, trust_score=trust_score))
                except Exception as chain_err:
                    logger.debug(f"条件連鎖評価スキップ: {chain_err}")
                return result
            result = PipelineResult(success=True, pipeline=pipeline_key, final_output=raw or {})
            await notify_pipeline_event(
                company_id=task.company_id,
                pipeline=pipeline_key,
                event_type="completed",
            )
            # ── Circuit Breaker 更新（raw が success 属性を持たない場合）──
            await _record_success(pipeline_key)
            return result
        except asyncio.TimeoutError:
            logger.error(f"パイプラインタイムアウト: {pipeline_key} ({PIPELINE_TIMEOUT_SECONDS}秒)")
            await notify_pipeline_event(task.company_id, pipeline_key, "error", {"error": f"タイムアウト({PIPELINE_TIMEOUT_SECONDS}秒)"})
            await _record_failure(pipeline_key, task.company_id)
            return PipelineResult(
                success=False,
                pipeline=pipeline_key,
                failed_step="timeout",
                final_output={"error": f"パイプラインが{PIPELINE_TIMEOUT_SECONDS}秒以内に完了しませんでした。"},
            )
        except Exception as e:
            logger.error(f"パイプライン実行エラー: {pipeline_key} — {e}")
            await notify_pipeline_event(task.company_id, pipeline_key, "error", {"error": str(e)})
            await _record_failure(pipeline_key, task.company_id)
            return PipelineResult(
                success=False,
                pipeline=pipeline_key,
                failed_step="pipeline_execution",
                final_output={"error": str(e)},
            )

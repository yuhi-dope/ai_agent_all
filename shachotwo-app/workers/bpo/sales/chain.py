"""パイプライン自動チェーン — パイプライン完了時に次パイプラインを自動起動する。

チェーンルール:
  ①lead_qualification (QUALIFIED) → ②proposal_generation
  ②proposal_generation (sent) → ③quotation_contract
  ③quotation_contract (signed) → ④customer_lifecycle (onboarding)
  ④customer_lifecycle (health_check, score<40) → cancellation_pipeline
  ④customer_lifecycle (health_check, score>=80) → ⑦upsell_briefing
  ⑤revenue_request (mode=revenue, 完了) → backoffice/invoice_issue（請求書発行）
  opportunity.stage=won → ⑧win_loss_feedback (outcome="won")
  opportunity.stage=lost → ⑧win_loss_feedback (outcome="lost")
  業種BPOパイプライン完了 → execution_logs に記録（proactive_scanner が次アクション判断）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# メインディスパッチャー
# ---------------------------------------------------------------------------


async def trigger_next_pipeline(
    pipeline_name: str,
    result: Any,
    company_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """パイプライン完了後の自動チェーンを実行する。

    呼び出し元をブロックしないよう asyncio.create_task で非同期起動する。
    チェーン失敗は上位パイプラインに伝播させない（非致命的）。

    Args:
        pipeline_name: 完了したパイプライン名
        result:        パイプラインの戻り値オブジェクト
        company_id:    シャチョツーテナントID（RLS用）
        context:       追加コンテキスト（opportunity_stage_changed 等で使用）

    Returns:
        {"triggered": [起動したパイプライン名のリスト]}
    """
    triggered: list[str] = []

    # ①→② リード判定(QUALIFIED) → 提案書生成
    if pipeline_name == "lead_qualification_pipeline":
        routing = getattr(result, "routing", "") or (result.final_output or {}).get("routing", "")
        if routing == "QUALIFIED":
            lead = (result.final_output or {}).get("lead", {})
            asyncio.create_task(_run_proposal_generation(company_id, lead))
            triggered.append("proposal_generation_pipeline")

    # ②→③ 提案書送付完了 → 見積・契約パイプライン
    # ただし proposal が "sent" ステータスになった場合のみ（ドラフト段階では起動しない）
    elif pipeline_name == "proposal_generation_pipeline":
        if hasattr(result, "final_output"):
            proposal_status = (result.final_output or {}).get("proposal", {}).get("status", "")
            # final_output 直下に status がある場合も考慮
            if not proposal_status:
                proposal_status = "sent" if (result.final_output or {}).get("email_sent") else "draft"
            opp_id = (result.final_output or {}).get("opportunity_id", "")
            if proposal_status == "sent" and opp_id:
                asyncio.create_task(_run_quotation_contract(company_id, opp_id))
                triggered.append("quotation_contract_pipeline")

    # ③→④ 契約署名完了 → 顧客ライフサイクル(onboarding)
    # NOTE: CloudSign Webhookからも呼ばれる（webhooks.pyから直接）
    elif pipeline_name == "quotation_contract_pipeline":
        if hasattr(result, "final_output"):
            contract = (result.final_output or {}).get("contract", {})
            if contract.get("status") == "signed":
                customer_id = contract.get("customer_id") or (result.final_output or {}).get("customer_id")
                if customer_id:
                    asyncio.create_task(_run_customer_onboarding(company_id, customer_id))
                    triggered.append("customer_lifecycle_pipeline(onboarding)")

    # ④→⑦ or ④→cancellation ヘルススコア結果による分岐
    elif pipeline_name == "customer_lifecycle_pipeline":
        if hasattr(result, "final_output"):
            health = (result.final_output or {}).get("health", {})
            score = health.get("score", 50)
            customer_id = (result.final_output or {}).get("customer_id", "")
            unused_modules = health.get("unused_modules", [])
            if score >= 80 and unused_modules:
                asyncio.create_task(_run_upsell_briefing(company_id, customer_id))
                triggered.append("upsell_briefing_pipeline")

    # ⑤→バックオフィス: MRR集計完了 → 請求書発行パイプライン
    elif pipeline_name == "revenue_request_pipeline":
        if hasattr(result, "final_output"):
            mode = (result.final_output or {}).get("mode", "")
            revenue = (result.final_output or {}).get("revenue_metrics", {})
            if mode == "revenue" and revenue:
                asyncio.create_task(_run_invoice_issue(company_id, revenue))
                triggered.append("backoffice/invoice_issue")

    # 業種BPO: 完了 → execution_logs に記録（proactive_scanner が次アクションを判断）
    elif pipeline_name in (
        "construction/estimation",
        "manufacturing/quoting",
        "nursing/care_billing",
        "realestate/rent_collection",
        "logistics/dispatch",
    ):
        asyncio.create_task(_log_bpo_completion(company_id, pipeline_name, result))
        triggered.append(f"bpo_completion_logged:{pipeline_name}")

    # 受注/失注 → ⑧学習
    elif pipeline_name == "opportunity_stage_changed":
        stage = (context or {}).get("stage", "")
        opp_id = (context or {}).get("opportunity_id", "")
        if stage in ("won", "lost"):
            asyncio.create_task(_run_win_loss_feedback(company_id, opp_id, stage))
            triggered.append(f"win_loss_feedback_pipeline(outcome={stage})")

    # ── GWS 逆同期: パイプライン結果を Google Workspace に反映 ──────
    try:
        from workers.gws.sync_engine import sync_pipeline_result
        asyncio.create_task(
            sync_pipeline_result(company_id, pipeline_name, result)
        )
    except ImportError:
        pass  # GWSモジュール未インストール時はスキップ
    except Exception as e:
        logger.warning(f"chain: GWS sync failed (non-fatal): {e}")

    # ログ記録
    if triggered:
        logger.info(f"chain triggered: {pipeline_name} → {triggered}")
        await _log_chain_event(company_id, pipeline_name, triggered)

    return {"triggered": triggered}


# ---------------------------------------------------------------------------
# 各パイプライン呼び出しラッパー
# ---------------------------------------------------------------------------


async def _run_proposal_generation(company_id: str, lead: dict[str, Any]) -> None:
    """提案書生成パイプラインを起動する（①→②）。"""
    try:
        from workers.bpo.sales.sfa.proposal_generation_pipeline import (
            run_proposal_generation_pipeline,
        )

        input_data = {
            "lead_id": lead.get("id"),
            "company_name": lead.get("company_name", ""),
            "industry": lead.get("industry", ""),
            "employee_count": lead.get("employee_count"),
            "pain_points": lead.get("pain_points", ""),
            "contact_email": lead.get("contact_email", ""),
            "contact_name": lead.get("contact_name", ""),
        }
        await run_proposal_generation_pipeline(
            company_id=company_id,
            input_data=input_data,
        )
        logger.info(f"chain: proposal_generation_pipeline 完了 lead_id={lead.get('id')}")
    except Exception as e:
        logger.error(f"chain: _run_proposal_generation 失敗（非致命的）: {e}")


async def _run_quotation_contract(company_id: str, opportunity_id: str) -> None:
    """見積・契約パイプラインを起動する（②→③）。"""
    try:
        from workers.bpo.sales.sfa.quotation_contract_pipeline import run_quotation_contract_pipeline
        await run_quotation_contract_pipeline(
            company_id=company_id,
            input_data={"opportunity_id": opportunity_id, "mode": "quotation"},
        )
        logger.info(f"chain: quotation_contract_pipeline 完了 opportunity_id={opportunity_id}")
    except Exception as e:
        logger.error(f"chain: _run_quotation_contract 失敗（非致命的）: {e}")


async def _run_customer_onboarding(company_id: str, customer_id: str) -> None:
    """顧客ライフサイクルパイプライン（onboardingモード）を起動する（③→④）。"""
    try:
        from workers.bpo.sales.crm.customer_lifecycle_pipeline import (
            run_customer_lifecycle_pipeline,
        )

        await run_customer_lifecycle_pipeline(
            company_id=company_id,
            customer_id=customer_id,
            mode="onboarding",
        )
        logger.info(f"chain: customer_lifecycle_pipeline(onboarding) 完了 customer_id={customer_id}")
    except Exception as e:
        logger.error(f"chain: _run_customer_onboarding 失敗（非致命的）: {e}")


async def _run_upsell_briefing(company_id: str, customer_id: str) -> None:
    """アップセルブリーフィングパイプラインを起動する（④→⑦）。"""
    try:
        from workers.bpo.sales.cs.upsell_briefing_pipeline import (
            run_upsell_briefing_pipeline,
        )

        await run_upsell_briefing_pipeline(
            company_id=company_id,
            customer_company_id=customer_id,
            input_data={"customer_id": customer_id},
        )
        logger.info(f"chain: upsell_briefing_pipeline 完了 customer_id={customer_id}")
    except Exception as e:
        logger.error(f"chain: _run_upsell_briefing 失敗（非致命的）: {e}")


async def _run_win_loss_feedback(
    company_id: str,
    opportunity_id: str,
    outcome: str,
) -> None:
    """受注/失注フィードバックパイプラインを起動する（→⑧）。"""
    try:
        from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
            run_win_loss_feedback_pipeline,
        )

        await run_win_loss_feedback_pipeline(
            company_id=company_id,
            input_data={
                "opportunity_id": opportunity_id,
                "outcome": outcome,
            },
        )
        logger.info(
            f"chain: win_loss_feedback_pipeline 完了 opportunity_id={opportunity_id} outcome={outcome}"
        )
    except Exception as e:
        logger.error(f"chain: _run_win_loss_feedback 失敗（非致命的）: {e}")


async def _run_invoice_issue(company_id: str, revenue_metrics: dict[str, Any]) -> None:
    """バックオフィス請求書発行パイプラインを起動する（⑤→backoffice）。

    revenue_request の mode="revenue" 完了時に自動チェーン。
    顧客企業への月次請求書をfreee経由で発行する。
    """
    try:
        from workers.bpo.manager.task_router import route_and_execute

        await route_and_execute(
            company_id=company_id,
            pipeline_key="backoffice/invoice_issue",
            input_data={
                "source": "revenue_request_chain",
                "revenue_metrics": revenue_metrics,
                "auto_triggered": True,
            },
        )
        logger.info(f"chain: backoffice/invoice_issue 完了 company_id={company_id}")
    except Exception as e:
        logger.error(f"chain: _run_invoice_issue 失敗（非致命的）: {e}")


# ---------------------------------------------------------------------------
# チェーンイベントログ
# ---------------------------------------------------------------------------


async def _log_bpo_completion(company_id: str, pipeline_name: str, result: Any) -> None:
    """業種BPOパイプラインの完了をexecution_logsに記録する。
    proactive_scannerがこのログを参照して次アクションを提案する。"""
    try:
        from db.supabase import get_service_client
        from datetime import datetime, timezone
        db = get_service_client()

        success = getattr(result, "success", True) if result else True

        db.table("execution_logs").insert({
            "company_id": company_id,
            "pipeline": pipeline_name,
            "step": "pipeline_completed",
            "status": "completed" if success else "failed",
            "payload": {
                "event": "bpo_pipeline_completed",
                "pipeline_name": pipeline_name,
                "success": success,
                "available_for_proactive": True,
            },
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"chain: bpo_completion logged pipeline={pipeline_name}")
    except Exception as e:
        logger.warning(f"_log_bpo_completion failed: {e}")


async def _log_chain_event(
    company_id: str,
    source_pipeline: str,
    triggered_pipelines: list[str],
) -> None:
    """execution_logs テーブルにチェーンイベントを記録する。"""
    try:
        db = get_service_client()
        db.table("execution_logs").insert({
            "company_id": company_id,
            "agent_name": "pipeline_chain",
            "input_hash": source_pipeline,
            "output": {
                "source_pipeline": source_pipeline,
                "triggered_pipelines": triggered_pipelines,
                "event": "chain_triggered",
            },
            "cost_yen": 0.0,
            "duration_ms": 0,
            "created_at": _now_iso(),
        }).execute()
    except Exception as e:
        # ログ失敗はチェーン全体を止めない
        logger.warning(f"chain: _log_chain_event 失敗（無視）: {e}")


def _now_iso() -> str:
    """現在時刻をISO8601形式で返す。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

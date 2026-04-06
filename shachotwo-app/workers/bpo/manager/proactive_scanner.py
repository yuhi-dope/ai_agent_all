"""BPO Manager — ProactiveScanner。「今やるべきこと」を能動的にスキャンする。"""
import logging
from typing import Any

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel

logger = logging.getLogger(__name__)

# proposal_type → ExecutionLevel のマッピング
_PROPOSAL_TYPE_TO_EXECUTION_LEVEL: dict[str, ExecutionLevel] = {
    "risk_alert": ExecutionLevel.NOTIFY_ONLY,
    "improvement": ExecutionLevel.DRAFT_CREATE,
    "opportunity": ExecutionLevel.DATA_COLLECT,
    "rule_challenge": ExecutionLevel.DRAFT_CREATE,  # デフォルトと同じ
}


async def scan_proactive_tasks(company_id: str) -> list[BPOTask]:
    """
    company_state_snapshots の最新スナップショットを取得し、
    knowledge_items の conditions と照合して能動タスクを生成する。

    brain/proactive/analyzer.py の analyze_and_propose() を活用して
    BPOTaskに変換する。
    """
    try:
        from db.supabase import get_service_client
        from brain.proactive.analyzer import analyze_and_propose

        db = get_service_client()

        # 能動提案を生成
        try:
            analysis = await analyze_and_propose(company_id=company_id)
        except Exception as e:
            logger.warning(
                f"proactive_scanner: analyze_and_propose failed for {company_id}: {e}",
                exc_info=True,
            )
            return await _scan_from_knowledge_items(company_id)

        proposals = analysis.proposals if analysis else []

        tasks: list[BPOTask] = []
        pending_proposals: list[dict[str, Any]] = []

        for proposal in proposals:
            meta = getattr(proposal, "metadata", {}) or {}
            pipeline = meta.get("pipeline", "")
            if not pipeline:
                continue

            # proposal_type に応じた execution_level を決定
            proposal_type = getattr(proposal, "proposal_type", "improvement")
            execution_level = _PROPOSAL_TYPE_TO_EXECUTION_LEVEL.get(
                proposal_type, ExecutionLevel.DRAFT_CREATE
            )

            impact_score = float(getattr(proposal, "impact_estimate", None) and
                                 getattr(proposal.impact_estimate, "confidence", 0.5) or 0.5)
            # impact_score は metadata から取得を優先（analyze_and_propose が設定している場合）
            if "impact_score" in meta:
                impact_score = float(meta["impact_score"])

            # 自動実行条件: impact_score < 0.5 かつ execution_level <= DATA_COLLECT
            can_auto_execute = (
                impact_score < 0.5
                and execution_level <= ExecutionLevel.DATA_COLLECT
            )

            if can_auto_execute:
                tasks.append(BPOTask(
                    company_id=company_id,
                    pipeline=pipeline,
                    trigger_type=TriggerType.PROACTIVE,
                    execution_level=execution_level,
                    input_data=meta.get("input_data", {}),
                    estimated_impact=impact_score,
                    knowledge_item_ids=[str(kid) for kid in (getattr(proposal, "related_knowledge_ids", []) or [])],
                ))
            else:
                # 承認待ちとして proactive_proposals テーブルに保存
                pending_proposals.append({
                    "company_id": company_id,
                    "proposal_type": proposal_type,
                    "pipeline": pipeline,
                    "execution_level": execution_level.value,
                    "impact_score": impact_score,
                    "input_data": meta.get("input_data", {}),
                    "status": "pending_approval",
                })

        if pending_proposals:
            await _save_pending_proposals(db, pending_proposals)

        logger.info(
            f"proactive_scanner: {len(tasks)} auto-tasks, "
            f"{len(pending_proposals)} pending-approval from "
            f"{len(proposals)} proposals for {company_id}"
        )

        # 自動実行対象のタスクを実行
        proposal_dicts = [
            {
                "impact_score": t.estimated_impact,
                "execution_level": t.execution_level.value,
                "metadata": {"pipeline": t.pipeline, "input_data": t.input_data},
            }
            for t in tasks
        ]
        auto_executed = await _auto_execute_proposals(company_id, proposal_dicts)
        if auto_executed:
            logger.info(f"proactive_scanner: auto-executed {len(auto_executed)} proposals for {company_id}")

        return tasks

    except ImportError:
        # brain/proactive が未実装の場合はknowledge_itemsから直接スキャン
        return await _scan_from_knowledge_items(company_id)
    except Exception as e:
        logger.error(f"proactive_scanner error: {e}")
        return []


async def _save_pending_proposals(db: Any, proposals: list[dict[str, Any]]) -> None:
    """承認待ち提案を proactive_proposals テーブルに保存する。"""
    try:
        rows = [
            {
                "company_id": p["company_id"],
                "proposal_type": p["proposal_type"],
                "title": p.get("pipeline", ""),
                "description": f"pipeline={p['pipeline']}, execution_level={p['execution_level']}",
                "status": p["status"],
            }
            for p in proposals
        ]
        db.table("proactive_proposals").insert(rows).execute()
    except Exception as e:
        logger.warning(f"proactive_scanner: failed to save pending proposals: {e}")


async def _auto_execute_proposals(company_id: str, proposals: list[dict[str, Any]]) -> list[str]:
    """信頼度の高い能動提案を自動実行する。

    条件: impact_score < 0.5 かつ execution_level <= 1 の提案のみ自動実行
    （影響度が低く、読み取り専用レベルの操作のみ自動化する）
    """
    executed: list[str] = []
    for proposal in proposals:
        # impact_score < 0.5 かつ execution_level <= 1 の提案のみ自動実行
        if proposal.get("impact_score", 1.0) < 0.5 and proposal.get("execution_level", 3) <= 1:
            meta = proposal.get("metadata", {})
            pipeline = meta.get("pipeline", "")
            input_data = meta.get("input_data", {})
            if pipeline:
                try:
                    from workers.bpo.manager.task_router import route_and_execute
                    from workers.bpo.manager.models import BPOTask, TriggerType

                    task = BPOTask(
                        company_id=company_id,
                        pipeline=pipeline,
                        input_data=input_data,
                        trigger_type=TriggerType.PROACTIVE,
                        context={"source": "proactive_scanner"},
                    )
                    await route_and_execute(task)
                    executed.append(pipeline)
                    logger.info(f"proactive_scanner: auto-executed pipeline={pipeline}")
                except Exception as e:
                    logger.warning(f"proactive auto-execute failed for {pipeline}: {e}")
    return executed


async def _scan_from_knowledge_items(company_id: str) -> list[BPOTask]:
    """
    フォールバック: knowledge_itemsのproactive_triggerメタデータから直接スキャン。
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        result = db.table("knowledge_items").select(
            "id, title, metadata, confidence"
        ).eq("company_id", company_id).eq("is_active", True).execute()

        items = result.data or []
        tasks: list[BPOTask] = []

        for item in items:
            meta = item.get("metadata") or {}
            if meta.get("trigger_type") != "proactive":
                continue

            pipeline = meta.get("pipeline", "")
            if not pipeline:
                continue

            # 条件チェック（簡易版）
            if not meta.get("condition_met", False):
                continue

            tasks.append(BPOTask(
                company_id=company_id,
                pipeline=pipeline,
                trigger_type=TriggerType.PROACTIVE,
                execution_level=ExecutionLevel.DRAFT_CREATE,
                input_data=meta.get("input_data", {}),
                estimated_impact=float(item.get("confidence", 0.6)),
                knowledge_item_ids=[item["id"]],
            ))

        return tasks

    except Exception as e:
        logger.error(f"proactive_scanner fallback error: {e}")
        return []

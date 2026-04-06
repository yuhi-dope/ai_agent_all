"""Proactive analysis engine — risk detection + improvement proposals.

Analyzes company knowledge base and state to generate proposals:
- risk_alert: Risks (key-person dependency, compliance gaps, cost overruns)
- improvement: Efficiency/cost/automation opportunities
- rule_challenge: Contradictions or outdated rules
- opportunity: Business opportunities
"""
import asyncio
import json
import logging
from brain.proactive.models import Proposal, ProactiveAnalysisResult
from brain.proactive.parsing import parse_proposals_from_llm_response
from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.extraction import SYSTEM_PROACTIVE

logger = logging.getLogger(__name__)

# LLM呼び出しタイムアウト（秒）
_LLM_TIMEOUT_SECONDS = 60


async def analyze_and_propose(
    company_id: str,
    department: str | None = None,
    max_knowledge_items: int = 50,
) -> ProactiveAnalysisResult:
    """Analyze knowledge base + company state → generate proactive proposals.

    1. Fetch recent knowledge items
    2. Fetch latest company state snapshot
    3. Send to LLM for analysis
    4. Parse proposals and save to DB
    """
    db = get_service_client()

    # 1. Fetch knowledge items
    q = db.table("knowledge_items") \
        .select("id, title, content, department, category, item_type, confidence") \
        .eq("company_id", company_id) \
        .eq("is_active", True) \
        .order("created_at", desc=True) \
        .limit(max_knowledge_items)

    if department:
        q = q.eq("department", department)

    knowledge_result = q.execute()
    items = knowledge_result.data or []

    if not items:
        logger.info(
            f"proactive_analyzer: no knowledge items found for company_id={company_id}, "
            "returning empty result"
        )
        return ProactiveAnalysisResult(
            proposals=[],
            model_used="none",
            cost_yen=0.0,
            knowledge_count=0,
        )

    # 2. Fetch latest state snapshot（存在しない場合でも分析を続行）
    state: dict | None = None
    try:
        state_result = db.table("company_state_snapshots") \
            .select("*") \
            .eq("company_id", company_id) \
            .order("snapshot_at", desc=True) \
            .limit(1) \
            .execute()
        state = state_result.data[0] if state_result.data else None
        if state is None:
            logger.info(
                f"proactive_analyzer: no company_state_snapshots for company_id={company_id}, "
                "proceeding with knowledge_items only"
            )
    except Exception as e:
        logger.warning(
            f"proactive_analyzer: failed to fetch company_state_snapshots for "
            f"company_id={company_id}: {e}. Proceeding with knowledge_items only."
        )

    # 3. Build context and call LLM with timeout
    context = _build_context(items, state)
    llm = get_llm_client()

    try:
        response = await asyncio.wait_for(
            llm.generate(LLMTask(
                messages=[
                    {"role": "system", "content": SYSTEM_PROACTIVE},
                    {"role": "user", "content": context},
                ],
                tier=ModelTier.FAST,
                task_type="proactive_analysis",
                company_id=company_id,
                max_tokens=4096,
            )),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"proactive_analyzer: LLM call timed out after {_LLM_TIMEOUT_SECONDS}s "
            f"for company_id={company_id}"
        )
        return ProactiveAnalysisResult(
            proposals=[],
            model_used="timeout",
            cost_yen=0.0,
            knowledge_count=len(items),
        )

    # 4. Parse proposals
    proposals = parse_proposals_from_llm_response(response.content, items)

    # 5. Save to DB
    await _save_proposals(db, company_id, proposals)

    return ProactiveAnalysisResult(
        proposals=proposals,
        model_used=response.model_used,
        cost_yen=response.cost_yen,
        knowledge_count=len(items),
    )


def _build_context(items: list[dict], state: dict | None) -> str:
    """Build analysis context from knowledge items and company state."""
    parts = ["## ナレッジベース\n"]

    for i, item in enumerate(items, 1):
        parts.append(
            f"[{i}] [{item['department']}] {item['title']} "
            f"(type={item['item_type']}, confidence={item.get('confidence', '?')})\n"
            f"  {item['content'][:300]}\n"
        )

    if state:
        parts.append("\n## 会社の現在状態\n")
        for dim in ["people_state", "process_state", "cost_state", "tool_state", "risk_state"]:
            val = state.get(dim)
            if val:
                label = dim.replace("_state", "")
                parts.append(f"- {label}: {json.dumps(val, ensure_ascii=False)[:500]}\n")
    else:
        parts.append("\n## 会社の現在状態\n（スナップショット未取得のため省略）\n")

    parts.append(
        "\n上記のナレッジと会社状態を分析し、リスク・改善機会・ルールの矛盾・ビジネス機会を検出してください。"
    )

    return "".join(parts)


async def _save_proposals(db, company_id: str, proposals: list[Proposal]) -> None:
    """Save proposals to proactive_proposals table."""
    if not proposals:
        return

    rows = [
        {
            "company_id": company_id,
            "proposal_type": p.proposal_type,
            "title": p.title,
            "description": p.description,
            "impact_estimate": p.impact_estimate.model_dump() if p.impact_estimate else None,
            "evidence": p.evidence.model_dump() if p.evidence else None,
            "related_knowledge_ids": [str(kid) for kid in p.related_knowledge_ids],
            "status": "proposed",
        }
        for p in proposals
    ]
    db.table("proactive_proposals").insert(rows).execute()

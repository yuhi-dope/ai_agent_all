"""Proactive analysis engine — risk detection + improvement proposals.

Analyzes company knowledge base and state to generate proposals:
- risk_alert: Risks (key-person dependency, compliance gaps, cost overruns)
- improvement: Efficiency/cost/automation opportunities
- rule_challenge: Contradictions or outdated rules
- opportunity: Business opportunities
"""
import json
import logging
from uuid import UUID

from brain.proactive.models import (
    Evidence,
    ImpactEstimate,
    Proposal,
    ProactiveAnalysisResult,
    Signal,
)
from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.extraction import SYSTEM_PROACTIVE

logger = logging.getLogger(__name__)


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
        return ProactiveAnalysisResult(
            proposals=[],
            model_used="none",
            cost_yen=0.0,
            knowledge_count=0,
        )

    # 2. Fetch latest state snapshot
    state_result = db.table("company_state_snapshots") \
        .select("*") \
        .eq("company_id", company_id) \
        .order("snapshot_at", desc=True) \
        .limit(1) \
        .execute()

    state = state_result.data[0] if state_result.data else None

    # 3. Build context and call LLM
    context = _build_context(items, state)
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": SYSTEM_PROACTIVE},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.FAST,
        task_type="proactive_analysis",
        company_id=company_id,
        max_tokens=4096,
    ))

    # 4. Parse proposals
    proposals = _parse_proposals(response.content, items)

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

    parts.append(
        "\n上記のナレッジと会社状態を分析し、リスク・改善機会・ルールの矛盾・ビジネス機会を検出してください。"
    )

    return "".join(parts)


def _extract_json(content: str) -> str:
    """Extract JSON from LLM response, handling markdown fences and surrounding text."""
    import re
    text = content.strip()

    # Try to extract from ```json ... ``` block
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()

    # Try to find JSON array or object directly
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        if start != -1:
            # Find matching end
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]

    return text


def _parse_proposals(content: str, items: list[dict]) -> list[Proposal]:
    """Parse LLM response into Proposal list."""
    try:
        text = _extract_json(content)
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]

        # Map knowledge IDs from items list
        item_ids = [UUID(it["id"]) for it in items]

        proposals = []
        for raw in data:
            impact = None
            if raw.get("impact_estimate"):
                ie = raw["impact_estimate"]
                impact = ImpactEstimate(
                    time_saved_hours=ie.get("time_saved_hours"),
                    cost_reduction_yen=ie.get("cost_reduction_yen"),
                    risk_reduction=ie.get("risk_reduction"),
                    confidence=ie.get("confidence", 0.5),
                    calculation_basis=ie.get("calculation_basis"),
                )

            evidence = None
            if raw.get("evidence") and raw["evidence"].get("signals"):
                evidence = Evidence(
                    signals=[
                        Signal(
                            source=s.get("source", "knowledge"),
                            value=s.get("value", ""),
                            score=s.get("score", 0.5),
                        )
                        for s in raw["evidence"]["signals"]
                    ]
                )

            proposals.append(Proposal(
                proposal_type=raw.get("type", "improvement"),
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                impact_estimate=impact,
                evidence=evidence,
                priority=raw.get("priority", "medium"),
                related_knowledge_ids=item_ids[:5],  # link to top items
            ))

        return proposals

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Proactive response parse failed: {e}, creating single proposal")
        return [Proposal(
            proposal_type="improvement",
            title="分析結果",
            description=content[:2000],
            priority="medium",
        )]


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

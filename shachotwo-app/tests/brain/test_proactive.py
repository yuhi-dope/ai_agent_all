"""Tests for brain/proactive module."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.proactive.parsing import parse_proposals_from_llm_response
MOCK_KNOWLEDGE_ITEMS = [
    {
        "id": str(uuid4()),
        "title": "見積もり承認ルール",
        "content": "100万円以上の見積もりは社長承認が必要",
        "department": "営業",
        "category": "pricing",
        "item_type": "rule",
        "confidence": 0.9,
    },
    {
        "id": str(uuid4()),
        "title": "経費精算の期限",
        "content": "経費精算は発生月の翌月10日までに申請する",
        "department": "経理",
        "category": "policy",
        "item_type": "rule",
        "confidence": 0.85,
    },
]

MOCK_LLM_PROPOSALS = json.dumps([
    {
        "type": "risk_alert",
        "title": "見積もり承認の属人化リスク",
        "description": "社長のみが承認権限を持つため、不在時に業務が停滞するリスクがあります",
        "impact_estimate": {
            "time_saved_hours": 5,
            "risk_reduction": 0.7,
            "confidence": 0.8,
            "calculation_basis": "月平均の見積もり件数から試算",
        },
        "evidence": {
            "signals": [{"source": "knowledge", "value": "承認が社長1人", "score": 0.9}]
        },
        "priority": "high",
    },
    {
        "type": "improvement",
        "title": "経費精算の自動化提案",
        "description": "経費精算プロセスをデジタル化し、申請・承認をワークフロー化することで処理時間を短縮できます",
        "impact_estimate": {
            "time_saved_hours": 10,
            "cost_reduction_yen": 50000,
            "confidence": 0.6,
            "calculation_basis": "月間経費精算件数×平均処理時間",
        },
        "priority": "medium",
    },
])


class TestParseProposals:
    def test_valid_json(self):
        proposals = parse_proposals_from_llm_response(MOCK_LLM_PROPOSALS, MOCK_KNOWLEDGE_ITEMS)
        assert len(proposals) == 2
        assert proposals[0].proposal_type == "risk_alert"
        assert proposals[0].title == "見積もり承認の属人化リスク"
        assert proposals[0].priority == "high"
        assert proposals[1].proposal_type == "improvement"

    def test_with_code_fences(self):
        wrapped = f"```json\n{MOCK_LLM_PROPOSALS}\n```"
        proposals = parse_proposals_from_llm_response(wrapped, MOCK_KNOWLEDGE_ITEMS)
        assert len(proposals) == 2

    def test_invalid_json_fallback(self):
        proposals = parse_proposals_from_llm_response("これは分析結果のテキストです", MOCK_KNOWLEDGE_ITEMS)
        assert len(proposals) == 1
        assert proposals[0].proposal_type == "improvement"
        assert proposals[0].title == "分析結果"
        assert "解釈できませんでした" in proposals[0].description

    def test_impact_estimate_parsing(self):
        proposals = parse_proposals_from_llm_response(MOCK_LLM_PROPOSALS, MOCK_KNOWLEDGE_ITEMS)
        impact = proposals[0].impact_estimate
        assert impact is not None
        assert impact.time_saved_hours == 5
        assert impact.risk_reduction == 0.7


class TestAnalyzeAndPropose:
    @pytest.mark.asyncio
    async def test_no_knowledge_returns_empty(self):
        pytest.importorskip("google.genai")
        from brain.proactive.analyzer import analyze_and_propose

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.analyzer.get_service_client", return_value=mock_db):
            result = await analyze_and_propose(str(uuid4()))

        assert len(result.proposals) == 0
        assert result.knowledge_count == 0

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        pytest.importorskip("google.genai")
        from brain.proactive.analyzer import analyze_and_propose

        mock_db = MagicMock()

        # Knowledge items query
        ki_chain = mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value
        ki_chain.execute.return_value = MagicMock(data=MOCK_KNOWLEDGE_ITEMS)

        # State snapshot query
        state_chain = mock_db.table.return_value.select.return_value.eq.return_value \
            .order.return_value.limit.return_value
        state_chain.execute.return_value = MagicMock(data=[])

        # Insert proposals
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_LLM_PROPOSALS,
            model_used="gemini-2.5-flash",
            cost_yen=0.02,
        )

        with patch("brain.proactive.analyzer.get_service_client", return_value=mock_db), \
             patch("brain.proactive.analyzer.get_llm_client", return_value=mock_llm):
            result = await analyze_and_propose(str(uuid4()))

        assert len(result.proposals) == 2
        assert result.model_used == "gemini-2.5-flash"
        assert result.knowledge_count == 2

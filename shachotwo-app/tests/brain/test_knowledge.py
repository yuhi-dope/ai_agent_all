"""Tests for brain/knowledge (embeddings, search, Q&A)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.knowledge.search import SearchResult, hybrid_search, keyword_search, vector_search
from brain.knowledge.qa import QAResult, answer_question, _parse_qa_response


MOCK_SEARCH_RESULTS = [
    SearchResult(
        item_id=uuid4(),
        title="見積もり承認ルール",
        content="100万円以上の見積もりは社長承認が必要",
        department="営業",
        category="pricing",
        item_type="rule",
        confidence=0.9,
        similarity=0.85,
    ),
    SearchResult(
        item_id=uuid4(),
        title="経費精算の期限",
        content="経費精算は発生月の翌月10日までに申請する",
        department="経理",
        category="policy",
        item_type="rule",
        confidence=0.85,
        similarity=0.72,
    ),
]


class TestVectorSearch:
    @pytest.mark.asyncio
    async def test_vector_search_calls_rpc(self):
        mock_embedding = [0.1] * 512
        mock_db = MagicMock()
        mock_db.rpc.return_value.execute.return_value = MagicMock(data=[
            {
                "id": str(uuid4()),
                "title": "テスト",
                "content": "テスト内容",
                "department": "営業",
                "category": "pricing",
                "item_type": "rule",
                "confidence": 0.9,
                "similarity": 0.85,
            }
        ])

        with patch("brain.knowledge.search.generate_query_embedding", new_callable=AsyncMock, return_value=mock_embedding), \
             patch("brain.knowledge.search.get_service_client", return_value=mock_db):
            results = await vector_search("見積もりのルールは？", str(uuid4()))

        assert len(results) == 1
        assert results[0].title == "テスト"
        mock_db.rpc.assert_called_once()


class TestKeywordSearch:
    @pytest.mark.asyncio
    async def test_keyword_search_ilike(self):
        mock_db = MagicMock()
        chain = mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.or_.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=[
            {
                "id": str(uuid4()),
                "title": "見積もり承認ルール",
                "content": "100万円以上は社長承認",
                "department": "営業",
                "category": "pricing",
                "item_type": "rule",
                "confidence": 0.9,
            }
        ])

        with patch("brain.knowledge.search.get_service_client", return_value=mock_db):
            results = await keyword_search("見積もり", str(uuid4()))

        assert len(results) == 1
        assert results[0].similarity == 0.5  # fixed score


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_hybrid_falls_back_to_keyword(self):
        """When vector returns too few results, keyword fills the gap."""
        vector_result = [MOCK_SEARCH_RESULTS[0]]
        keyword_result = [MOCK_SEARCH_RESULTS[1]]

        with patch("brain.knowledge.search.vector_search", new_callable=AsyncMock, return_value=vector_result), \
             patch("brain.knowledge.search.keyword_search", new_callable=AsyncMock, return_value=keyword_result):
            results = await hybrid_search("テスト", str(uuid4()), top_k=5)

        assert len(results) == 2


class TestParseQAResponse:
    def test_valid_json(self):
        content = json.dumps({
            "answer": "100万円以上の見積もりは社長承認が必要です。",
            "confidence": 0.9,
            "sources": [
                {"knowledge_id": str(uuid4()), "title": "見積もりルール", "relevance": 0.85}
            ],
            "missing_info": None,
        })
        result = _parse_qa_response(content, "gemini-2.5-flash", 0.01, MOCK_SEARCH_RESULTS)
        assert result.answer == "100万円以上の見積もりは社長承認が必要です。"
        assert result.confidence == 0.9

    def test_plain_text_fallback(self):
        result = _parse_qa_response(
            "これは普通のテキスト回答です。",
            "gemini-2.5-flash",
            0.01,
            MOCK_SEARCH_RESULTS,
        )
        assert result.answer == "これは普通のテキスト回答です。"
        assert result.confidence == 0.5
        assert len(result.sources) == 2  # from search results


class TestAnswerQuestion:
    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self):
        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=[]):
            result = await answer_question("テスト質問", str(uuid4()))

        assert result.confidence == 0.0
        assert "登録されていません" in result.answer

    @pytest.mark.asyncio
    async def test_answer_with_results(self):
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=json.dumps({
                "answer": "回答テスト",
                "confidence": 0.8,
                "sources": [],
                "missing_info": None,
            }),
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=MOCK_SEARCH_RESULTS), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question("見積もりのルールは？", str(uuid4()))

        assert result.answer == "回答テスト"
        assert result.confidence == 0.8

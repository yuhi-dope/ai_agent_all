"""E2E統合テスト — ナレッジ入力→構造化→embedding→Q&A回答の全フロー。

モック版（CI用）と実環境版（--run-integration）の2段階。
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class TestE2EPipelineMock:
    """モック環境でのE2Eフロー検証"""

    @pytest.mark.asyncio
    async def test_text_input_to_extraction(self):
        """テキスト入力 → LLM構造化 → ExtractionResult で返る"""
        from brain.extraction.pipeline import extract_knowledge
        from brain.extraction.models import ExtractionResult

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=json.dumps([{
                "title": "見積もりルール",
                "content": "100万円以上は社長承認が必要",
                "category": "pricing",
                "item_type": "rule",
                "department": "営業",
                "confidence": 0.9,
            }]),
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        session_id = str(uuid4())
        mock_db = MagicMock()
        # insert → session作成
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": session_id}]
        )
        # update → session完了
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])

        with patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm), \
             patch("brain.extraction.pipeline.get_service_client", return_value=mock_db):
            result = await extract_knowledge(
                text="うちの会社では、100万円以上の見積もりは社長承認が必要です。",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

        assert isinstance(result, ExtractionResult)

    @pytest.mark.asyncio
    async def test_search_returns_relevant_results(self):
        """検索が関連するナレッジを返す"""
        from brain.knowledge.search import SearchResult, hybrid_search

        mock_results = [
            SearchResult(
                item_id=uuid4(),
                title="見積もり承認ルール",
                content="100万円以上の見積もりは社長承認が必要",
                department="営業",
                category="pricing",
                item_type="rule",
                confidence=0.9,
                similarity=0.88,
            ),
        ]

        keyword_results = []

        with patch("brain.knowledge.search.vector_search", new_callable=AsyncMock, return_value=mock_results), \
             patch("brain.knowledge.search.keyword_search", new_callable=AsyncMock, return_value=keyword_results):
            results = await hybrid_search("見積もりの承認は誰がする？", str(uuid4()))

        assert len(results) >= 1
        assert "見積もり" in results[0].title

    @pytest.mark.asyncio
    async def test_full_pipeline_text_to_answer(self):
        """テキスト入力 → 構造化 → 検索 → Q&A回答の全フロー"""
        from brain.knowledge.qa import answer_question
        from brain.knowledge.search import SearchResult

        company_id = str(uuid4())

        # Step 1: 抽出結果をシミュレート（DBモック不要）
        extracted_item = {
            "title": "経費精算ルール",
            "content": "経費精算は発生月の翌月10日までに申請する。5万円以下は部門長、5万円超は社長承認。",
            "category": "policy",
            "item_type": "rule",
            "department": "経理",
            "confidence": 0.9,
        }

        # Step 2: 検索 → Q&A（モック）
        search_results = [
            SearchResult(
                item_id=uuid4(),
                title=extracted_item["title"],
                content=extracted_item["content"],
                department=extracted_item["department"],
                category=extracted_item["category"],
                item_type=extracted_item["item_type"],
                confidence=0.9,
                similarity=0.9,
            ),
        ]

        mock_qa_llm = AsyncMock()
        mock_qa_llm.generate.return_value = MagicMock(
            content=json.dumps({
                "answer": "経費精算は発生月の翌月10日までに申請してください。5万円以下は部門長承認、5万円超は社長承認が必要です。",
                "confidence": 0.9,
                "sources": [],
                "missing_info": None,
            }),
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=search_results), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_qa_llm):
            result = await answer_question("経費精算の期限は？", company_id)

        # 検証: 入力したナレッジに基づく回答が返る
        assert "翌月10日" in result.answer
        assert result.confidence >= 0.5
        assert len(result.sources) >= 1


class TestGenomeTemplateApplication:
    """テンプレート適用のテスト"""

    def test_construction_template_loads(self):
        """建設業テンプレートが読み込める"""
        from brain.genome.templates import load_templates, get_template, list_templates

        load_templates()
        template = get_template("construction")
        assert template is not None
        assert template.name == "建設業"
        assert len(template.departments) >= 4

    def test_construction_template_has_items(self):
        """建設業テンプレートに十分なナレッジアイテムがある"""
        from brain.genome.templates import load_templates, get_template, list_templates

        load_templates()
        template = get_template("construction")
        assert template is not None
        assert template.total_items >= 15, f"Only {template.total_items} items"

    def test_all_templates_load(self):
        """全業種テンプレートが読み込める"""
        from brain.genome.templates import load_templates, get_template, list_templates

        load_templates()
        templates = list_templates()
        ids = {t.id for t in templates}
        assert "construction" in ids
        assert "manufacturing" in ids
        assert "dental" in ids

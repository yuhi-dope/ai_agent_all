"""Tests for brain/extraction pipeline."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from brain.extraction.models import ExtractedItem, ExtractionResult
from brain.extraction.pipeline import _parse_items, extract_knowledge


MOCK_LLM_RESPONSE_JSON = json.dumps([
    {
        "title": "見積もり承認ルール",
        "content": "100万円以上の見積もりは社長承認が必要",
        "category": "pricing",
        "item_type": "rule",
        "department": "営業",
        "conditions": ["見積もり金額100万円以上"],
        "examples": ["大型案件の見積もり"],
        "exceptions": ["既存顧客のリピート案件"],
        "confidence": 0.9,
    },
    {
        "title": "経費精算の期限",
        "content": "経費精算は発生月の翌月10日までに申請する",
        "category": "policy",
        "item_type": "rule",
        "department": "経理",
        "conditions": None,
        "examples": None,
        "exceptions": None,
        "confidence": 0.85,
    },
])


class TestParseItems:
    def test_valid_json_array(self):
        items = _parse_items(MOCK_LLM_RESPONSE_JSON)
        assert items is not None
        assert len(items) == 2
        assert items[0].title == "見積もり承認ルール"
        assert items[1].confidence == 0.85

    def test_json_with_code_fences(self):
        wrapped = f"```json\n{MOCK_LLM_RESPONSE_JSON}\n```"
        items = _parse_items(wrapped)
        assert items is not None
        assert len(items) == 2

    def test_json_with_items_key(self):
        wrapped = json.dumps({"items": json.loads(MOCK_LLM_RESPONSE_JSON)})
        items = _parse_items(wrapped)
        assert items is not None
        assert len(items) == 2

    def test_invalid_json(self):
        items = _parse_items("This is not JSON at all")
        assert items is None

    def test_invalid_structure(self):
        items = _parse_items('"just a string"')
        assert items is None


class TestExtractKnowledge:
    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        session_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": session_id}]
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_LLM_RESPONSE_JSON,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.extraction.pipeline.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm):
            result = await extract_knowledge(
                text="見積もりは100万円以上なら社長承認。経費精算は翌月10日まで。",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

        assert isinstance(result, ExtractionResult)
        assert len(result.items) == 2
        assert result.model_used == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_retry_on_parse_failure(self):
        session_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": session_id}]
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        # First call returns invalid JSON, second returns valid
        mock_llm.generate.side_effect = [
            MagicMock(content="I couldn't parse that properly", model_used="gemini-2.5-flash", cost_yen=0.01),
            MagicMock(content=MOCK_LLM_RESPONSE_JSON, model_used="gemini-2.5-flash", cost_yen=0.01),
        ]

        with patch("brain.extraction.pipeline.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm):
            result = await extract_knowledge(
                text="テスト入力",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

        assert len(result.items) == 2
        assert mock_llm.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_department_override(self):
        session_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": session_id}]
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_LLM_RESPONSE_JSON,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.extraction.pipeline.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm):
            result = await extract_knowledge(
                text="テスト",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
                department="総務",
            )

        # All items should have department overridden
        for item in result.items:
            assert item.department == "総務"

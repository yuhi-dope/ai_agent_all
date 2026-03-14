"""Tests for brain/ingestion module."""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.ingestion.file import _extract_csv, ingest_file
from brain.ingestion.text import ingest_text


MOCK_EXTRACTION_ITEMS = json.dumps([
    {
        "title": "テストナレッジ",
        "content": "テスト内容です",
        "category": "policy",
        "item_type": "rule",
        "department": "総務",
        "conditions": None,
        "examples": None,
        "exceptions": None,
        "confidence": 0.8,
    }
])


class TestTextIngestion:
    @pytest.mark.asyncio
    async def test_ingest_text_delegates_to_extraction(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": str(uuid4())}]
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_EXTRACTION_ITEMS,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.extraction.pipeline.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm):
            result = await ingest_text(
                content="テスト入力テキスト",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

        assert len(result.items) == 1
        assert result.items[0].title == "テストナレッジ"


class TestCSVExtraction:
    def test_extract_csv_basic(self):
        csv_content = "名前,部署,役職\n田中太郎,営業,課長\n鈴木花子,経理,主任".encode("utf-8")
        result = _extract_csv(csv_content)
        assert "田中太郎" in result
        assert "営業" in result

    def test_extract_csv_empty(self):
        result = _extract_csv(b"")
        assert result == ""


class TestFileIngestion:
    @pytest.mark.asyncio
    async def test_ingest_text_file(self):
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": str(uuid4())}]
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_EXTRACTION_ITEMS,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.ingestion.file.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_service_client", return_value=mock_db), \
             patch("brain.extraction.pipeline.get_llm_client", return_value=mock_llm):
            result = await ingest_file(
                file_content=b"This is test content for knowledge extraction.",
                filename="test.txt",
                content_type="text/plain",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_ingest_unsupported_image(self):
        with pytest.raises(ValueError, match="Image OCR is not yet supported"):
            await ingest_file(
                file_content=b"\x89PNG\r\n",
                filename="photo.png",
                content_type="image/png",
                company_id=str(uuid4()),
                user_id=str(uuid4()),
            )

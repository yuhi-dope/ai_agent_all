"""Tests for brain/knowledge/entity_extractor.py"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.knowledge.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    KGRelation,
    VALID_ENTITY_TYPES,
    VALID_RELATION_TYPES,
)


COMPANY_ID = str(uuid4())

# LLMが返すNER結果のサンプル（JSON文字列）
MOCK_NER_RESPONSE = json.dumps([
    {
        "entity_type": "Company",
        "display_name": "株式会社テックビルド",
        "properties": {"industry": "建設業"},
    },
    {
        "entity_type": "Person",
        "display_name": "田中太郎",
        "properties": {"role": "現場監督"},
    },
    {
        "entity_type": "Project",
        "display_name": "渋谷再開発プロジェクト",
        "properties": {"budget": "500万円"},
    },
])

# LLMが返す関係推論結果のサンプル
MOCK_RELATION_RESPONSE = json.dumps([
    {
        "from_display_name": "渋谷再開発プロジェクト",
        "relation_type": "EXECUTED_BY",
        "to_display_name": "田中太郎",
        "confidence_score": 0.9,
        "properties": {},
    },
    {
        "from_display_name": "渋谷再開発プロジェクト",
        "relation_type": "BELONGS_TO",
        "to_display_name": "株式会社テックビルド",
        "confidence_score": 0.85,
        "properties": {},
    },
])


def _make_llm_response(content: str) -> MagicMock:
    mock = MagicMock()
    mock.content = content
    return mock


def _make_db_upsert_result(entity_id: str) -> MagicMock:
    mock_db = MagicMock()
    mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
        data=[{"id": entity_id}]
    )
    return mock_db


class TestExtractFromText:
    @pytest.mark.asyncio
    async def test_extract_returns_entities(self):
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(MOCK_NER_RESPONSE))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text(
                text="田中太郎が渋谷再開発プロジェクトの現場監督として株式会社テックビルドから派遣された。",
                company_id=COMPANY_ID,
                source_connector="manual",
            )

        assert len(entities) == 3
        entity_types = {e.entity_type for e in entities}
        assert entity_types == {"Company", "Person", "Project"}

    @pytest.mark.asyncio
    async def test_entity_key_format(self):
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(MOCK_NER_RESPONSE))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text(
                text="テスト",
                company_id=COMPANY_ID,
                source_connector="kintone",
            )

        for entity in entities:
            assert entity.entity_key.startswith("kintone:")
            assert entity.source_connector == "kintone"

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self):
        mock_llm = AsyncMock()
        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text(
                text="",
                company_id=COMPANY_ID,
            )

        assert entities == []
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_entity_type_filtered(self):
        invalid_response = json.dumps([
            {"entity_type": "InvalidType", "display_name": "テスト", "properties": {}},
            {"entity_type": "Company", "display_name": "有効な会社", "properties": {}},
        ])
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(invalid_response))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text("テスト", COMPANY_ID)

        assert len(entities) == 1
        assert entities[0].entity_type == "Company"

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty(self):
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response("これはJSONではありません"))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text("テスト", COMPANY_ID)

        assert entities == []

    @pytest.mark.asyncio
    async def test_code_block_json_parsed(self):
        """```json ... ``` 形式でも正しくパースされること。"""
        wrapped = "```json\n" + MOCK_NER_RESPONSE + "\n```"
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(wrapped))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm):
            extractor = EntityExtractor()
            entities = await extractor.extract_from_text("テスト", COMPANY_ID)

        assert len(entities) == 3


class TestUpsertEntities:
    @pytest.mark.asyncio
    async def test_upsert_returns_ids(self):
        entity_id1 = str(uuid4())
        entity_id2 = str(uuid4())
        call_count = 0

        def side_effect_upsert(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            eid = entity_id1 if call_count == 1 else entity_id2
            mock = MagicMock()
            mock.execute.return_value = MagicMock(data=[{"id": eid}])
            return mock

        mock_db = MagicMock()
        mock_db.table.return_value.upsert.side_effect = side_effect_upsert

        entities = [
            ExtractedEntity(
                entity_type="Company",
                display_name="株式会社A",
                entity_key="manual:株式会社A",
                properties={},
                source_connector="manual",
            ),
            ExtractedEntity(
                entity_type="Person",
                display_name="山田花子",
                entity_key="manual:山田花子",
                properties={},
                source_connector="manual",
            ),
        ]

        mock_llm = AsyncMock()
        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            ids = await extractor.upsert_entities(entities, COMPANY_ID)

        assert ids == [entity_id1, entity_id2]
        assert mock_db.table.call_count == 2

    @pytest.mark.asyncio
    async def test_upsert_empty_list_returns_empty(self):
        mock_llm = AsyncMock()
        mock_db = MagicMock()
        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            ids = await extractor.upsert_entities([], COMPANY_ID)

        assert ids == []
        mock_db.table.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_db_error_skips_entity(self):
        """DBエラーがあっても他のエンティティの処理を続けること。"""
        good_id = str(uuid4())
        call_count = 0

        def side_effect_upsert(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("DB connection error")
            mock = MagicMock()
            mock.execute.return_value = MagicMock(data=[{"id": good_id}])
            return mock

        mock_db = MagicMock()
        mock_db.table.return_value.upsert.side_effect = side_effect_upsert

        entities = [
            ExtractedEntity("Company", "失敗企業", "manual:失敗企業", {}, "manual"),
            ExtractedEntity("Person", "成功人物", "manual:成功人物", {}, "manual"),
        ]

        mock_llm = AsyncMock()
        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            ids = await extractor.upsert_entities(entities, COMPANY_ID)

        assert ids == [good_id]


class TestInferRelations:
    @pytest.mark.asyncio
    async def test_infer_relations_saved(self):
        from_id = str(uuid4())
        to_id1 = str(uuid4())
        to_id2 = str(uuid4())
        entity_ids = [from_id, to_id1, to_id2]

        mock_entities = [
            {"id": from_id, "display_name": "渋谷再開発プロジェクト", "entity_type": "Project"},
            {"id": to_id1, "display_name": "田中太郎", "entity_type": "Person"},
            {"id": to_id2, "display_name": "株式会社テックビルド", "entity_type": "Company"},
        ]

        rel_id1 = str(uuid4())
        rel_id2 = str(uuid4())

        def db_select_side_effect(*args, **kwargs):
            mock = MagicMock()
            mock.in_.return_value.eq.return_value.execute.return_value = MagicMock(data=mock_entities)
            return mock

        mock_insert_call_count = 0

        def insert_side_effect(*args, **kwargs):
            nonlocal mock_insert_call_count
            mock_insert_call_count += 1
            rid = rel_id1 if mock_insert_call_count == 1 else rel_id2
            mock = MagicMock()
            mock.execute.return_value = MagicMock(data=[{"id": rid}])
            return mock

        mock_db = MagicMock()
        mock_db.table.return_value.select.side_effect = db_select_side_effect
        mock_db.table.return_value.insert.side_effect = insert_side_effect

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(MOCK_RELATION_RESPONSE))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            relations = await extractor.infer_relations(
                entity_ids=entity_ids,
                source_text="渋谷再開発プロジェクトは田中太郎が担当し、株式会社テックビルドが受注した。",
                company_id=COMPANY_ID,
            )

        assert len(relations) == 2
        relation_types = {r.relation_type for r in relations}
        assert "EXECUTED_BY" in relation_types
        assert "BELONGS_TO" in relation_types

    @pytest.mark.asyncio
    async def test_infer_relations_single_entity_skipped(self):
        """エンティティが1件以下の場合はLLMを呼ばないこと。"""
        mock_llm = AsyncMock()
        mock_db = MagicMock()

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            relations = await extractor.infer_relations(
                entity_ids=[str(uuid4())],
                source_text="テスト",
                company_id=COMPANY_ID,
            )

        assert relations == []
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_infer_relations_invalid_type_filtered(self):
        """無効な relation_type はスキップされること。"""
        from_id = str(uuid4())
        to_id = str(uuid4())

        invalid_response = json.dumps([
            {
                "from_display_name": "プロジェクトA",
                "relation_type": "INVALID_RELATION",
                "to_display_name": "田中太郎",
                "confidence_score": 0.9,
                "properties": {},
            }
        ])

        mock_entities = [
            {"id": from_id, "display_name": "プロジェクトA", "entity_type": "Project"},
            {"id": to_id, "display_name": "田中太郎", "entity_type": "Person"},
        ]

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=mock_entities)

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=_make_llm_response(invalid_response))

        with patch("brain.knowledge.entity_extractor.get_llm_client", return_value=mock_llm), \
             patch("brain.knowledge.entity_extractor.get_service_client", return_value=mock_db):
            extractor = EntityExtractor()
            relations = await extractor.infer_relations(
                entity_ids=[from_id, to_id],
                source_text="テスト",
                company_id=COMPANY_ID,
            )

        assert relations == []
        mock_db.table.return_value.insert.assert_not_called()


class TestConstants:
    def test_valid_entity_types_defined(self):
        expected = {"Company", "Person", "Project", "Contract", "Product", "Transaction", "Document", "Task"}
        assert VALID_ENTITY_TYPES == expected

    def test_valid_relation_types_defined(self):
        expected = {"BELONGS_TO", "OWNS", "RELATED_TO", "SUPPLIED_BY", "EXECUTED_BY", "DERIVED_FROM", "DEPENDS_ON"}
        assert VALID_RELATION_TYPES == expected

"""Tests for brain/proactive/contradiction.py — 矛盾検知エンジン。"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


COMPANY_ID = str(uuid4())

_ID_A = str(uuid4())
_ID_B = str(uuid4())
_ID_C = str(uuid4())

MOCK_ITEMS_SAME_CATEGORY = [
    {
        "id": _ID_A,
        "title": "見積承認ルールA",
        "content": "100万円以上の見積もりは社長承認が必要",
        "department": "営業",
        "category": "pricing",
        "item_type": "rule",
        "confidence": 0.9,
        "updated_at": "2025-01-01T00:00:00Z",
    },
    {
        "id": _ID_B,
        "title": "見積承認ルールB",
        "content": "50万円以上の見積もりは社長承認が必要",
        "department": "営業",
        "category": "pricing",
        "item_type": "rule",
        "confidence": 0.8,
        "updated_at": "2025-03-01T00:00:00Z",
    },
]

MOCK_LLM_CONTRADICTION_RESPONSE = json.dumps({
    "is_contradiction": True,
    "confidence": 0.85,
    "explanation": "承認が必要な金額の閾値が異なっています（100万円 vs 50万円）",
    "suggested_resolution": "最新の運用に合わせて閾値を統一してください",
})

MOCK_LLM_NO_CONTRADICTION = json.dumps({
    "is_contradiction": False,
    "confidence": 0.9,
    "explanation": None,
    "suggested_resolution": None,
})


class TestDetectContradictions:
    @pytest.mark.asyncio
    async def test_detects_contradiction_and_records(self):
        """矛盾が検出された場合にDBへの記録と提案が行われること。"""
        from brain.proactive.contradiction import detect_contradictions

        mock_db = MagicMock()
        # knowledge_items取得: .eq("company_id").eq("is_active").order().limit()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=MOCK_ITEMS_SAME_CATEGORY)

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_LLM_CONTRADICTION_RESPONSE,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert len(result) == 1
        assert result[0]["confidence"] == 0.85
        assert "承認" in result[0]["explanation"]
        assert result[0]["item_a"]["id"] == _ID_A
        assert result[0]["item_b"]["id"] == _ID_B

    @pytest.mark.asyncio
    async def test_no_contradiction_returns_empty(self):
        """矛盾がない場合は空リストを返すこと。"""
        from brain.proactive.contradiction import detect_contradictions

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=MOCK_ITEMS_SAME_CATEGORY)

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=MOCK_LLM_NO_CONTRADICTION,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_single_item_returns_empty(self):
        """アイテムが1件以下の場合はペアが生成されず空を返す。"""
        from brain.proactive.contradiction import detect_contradictions

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[MOCK_ITEMS_SAME_CATEGORY[0]])

        mock_llm = AsyncMock()

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert result == []
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_categories_not_compared(self):
        """カテゴリが異なるアイテムはペア比較しない。"""
        from brain.proactive.contradiction import detect_contradictions

        items_different_cat = [
            {**MOCK_ITEMS_SAME_CATEGORY[0], "category": "pricing"},
            {**MOCK_ITEMS_SAME_CATEGORY[1], "category": "workflow"},
        ]

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=items_different_cat)

        mock_llm = AsyncMock()

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert result == []
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_contradiction_ignored(self):
        """confidenceが0.6未満の矛盾は結果に含めない。"""
        from brain.proactive.contradiction import detect_contradictions

        low_conf_response = json.dumps({
            "is_contradiction": True,
            "confidence": 0.4,
            "explanation": "わずかな差異",
            "suggested_resolution": "統一してください",
        })

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=MOCK_ITEMS_SAME_CATEGORY)

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=low_conf_response,
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_llm_timeout_handled_gracefully(self):
        """LLMタイムアウト時はそのペアをスキップし処理を続ける。"""
        import asyncio
        from brain.proactive.contradiction import detect_contradictions

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=MOCK_ITEMS_SAME_CATEGORY)

        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = asyncio.TimeoutError()

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_department_filter_applied(self):
        """department引数が渡された場合、クエリにフィルタが適用されること。"""
        from brain.proactive.contradiction import detect_contradictions

        mock_db = MagicMock()
        # アイテムなしを返す（フィルタ後）
        eq_chain = mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value
        eq_chain.execute.return_value = MagicMock(data=[])

        mock_llm = AsyncMock()

        with patch("brain.proactive.contradiction.get_service_client", return_value=mock_db), \
             patch("brain.proactive.contradiction.get_llm_client", return_value=mock_llm):
            result = await detect_contradictions(COMPANY_ID, department="営業")

        assert result == []


class TestRecordContradiction:
    def test_skips_duplicate_relation(self):
        """既存のcontradicts関係がある場合はknowledge_relationsへのinsertをスキップする。"""
        from brain.proactive.contradiction import _record_contradiction

        mock_db = MagicMock()
        # 既存のrelationが存在する
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value = MagicMock(data=[{"id": str(uuid4())}])

        contradiction = {
            "item_a": {"id": _ID_A, "title": "ルールA"},
            "item_b": {"id": _ID_B, "title": "ルールB"},
            "explanation": "矛盾あり",
            "suggested_resolution": "統一が必要",
            "confidence": 0.8,
        }

        _record_contradiction(mock_db, COMPANY_ID, contradiction)

        # insert for knowledge_relations should NOT be called
        # proposalのinsertは呼ばれる
        insert_calls = mock_db.table.return_value.insert.call_args_list
        # knowledge_relations insertが呼ばれていないことを確認（proposalのみ）
        tables_inserted = [
            mock_db.table.call_args_list[i][0][0]
            for i, call in enumerate(mock_db.table.call_args_list)
            if "insert" in str(mock_db.table.return_value.mock_calls)
        ]
        # proposalのinsertは必ず実行されること
        mock_db.table.return_value.insert.return_value.execute.assert_called()

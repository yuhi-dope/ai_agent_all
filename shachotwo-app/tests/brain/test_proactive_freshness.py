"""Tests for brain/proactive/freshness.py — 鮮度管理エンジン。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.proactive.freshness import FRESHNESS_THRESHOLDS, detect_stale_knowledge


COMPANY_ID = str(uuid4())
_ID_STALE = str(uuid4())
_ID_FRESH = str(uuid4())


def _make_item(
    item_id: str,
    category: str,
    days_old: int,
    title: str = "テストナレッジ",
) -> dict:
    updated = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "id": item_id,
        "title": title,
        "department": "営業",
        "category": category,
        "item_type": "rule",
        "confidence": 0.9,
        "updated_at": updated,
        "content": "テスト内容" * 20,  # 200文字超
    }


class TestDetectStaleKnowledge:
    @pytest.mark.asyncio
    async def test_detects_stale_pricing_after_90_days(self):
        """pricingカテゴリは90日超過で鮮度切れと判定されること。"""
        mock_db = MagicMock()
        stale_item = _make_item(_ID_STALE, "pricing", 100, "価格表ルール")
        fresh_item = _make_item(_ID_FRESH, "pricing", 30, "最新価格表")

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[stale_item, fresh_item])

        # 既存提案なし
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.contains.return_value \
            .execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert len(result) == 1
        assert result[0]["item"]["id"] == _ID_STALE
        assert result[0]["days_since_update"] >= 100
        assert result[0]["threshold_days"] == 90
        assert result[0]["staleness_score"] > 1.0

    @pytest.mark.asyncio
    async def test_fresh_items_not_detected(self):
        """閾値以内のアイテムは鮮度切れとして検出されないこと。"""
        mock_db = MagicMock()
        fresh_item = _make_item(_ID_FRESH, "pricing", 30, "新鮮な価格表")

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[fresh_item])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_sorted_by_staleness_score_descending(self):
        """staleness_score 降順でソートされること。"""
        mock_db = MagicMock()
        items = [
            _make_item(str(uuid4()), "pricing", 100, "やや古い"),  # staleness=100/90≒1.1
            _make_item(str(uuid4()), "pricing", 270, "かなり古い"),  # staleness=270/90=3.0
            _make_item(str(uuid4()), "pricing", 180, "中程度"),    # staleness=180/90=2.0
        ]

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=items)

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.contains.return_value \
            .execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert len(result) == 3
        scores = [r["staleness_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_staleness_score_capped_at_3(self):
        """staleness_scoreは3.0を超えないこと（キャップあり）。"""
        mock_db = MagicMock()
        very_old = _make_item(_ID_STALE, "pricing", 1000, "非常に古いルール")

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[very_old])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.contains.return_value \
            .execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert result[0]["staleness_score"] == 3.0

    @pytest.mark.asyncio
    async def test_default_threshold_for_unknown_category(self):
        """未定義カテゴリはデフォルト閾値（365日）が使用されること。"""
        mock_db = MagicMock()
        item = _make_item(_ID_STALE, "unknown_category", 400, "謎のルール")

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[item])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.contains.return_value \
            .execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert len(result) == 1
        assert result[0]["threshold_days"] == FRESHNESS_THRESHOLDS["__default__"]

    @pytest.mark.asyncio
    async def test_skips_existing_proposal(self):
        """既存の提案がある場合はproposalのinsertをスキップすること。"""
        mock_db = MagicMock()
        stale = _make_item(_ID_STALE, "pricing", 200, "古い価格表")

        # knowledge_items クエリ
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[stale])

        # 既存の提案が存在する（Pythonフィルタ用: related_knowledge_idsに対象IDを含む）
        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .execute.return_value = MagicMock(data=[{
                "id": str(uuid4()),
                "related_knowledge_ids": [str(_ID_STALE)],
            }])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        # 検出はされるが、insertは呼ばれない
        assert len(result) == 1
        mock_db.table.return_value.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_department_filter(self):
        """department引数が渡された場合、クエリにフィルタが適用されること。"""
        mock_db = MagicMock()

        eq_chain = mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value
        eq_chain.execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID, department="経理")

        assert result == []

    @pytest.mark.asyncio
    async def test_content_preview_truncated_to_200(self):
        """content_previewが200文字以内に切り詰められること。"""
        mock_db = MagicMock()
        long_content_item = {
            **_make_item(_ID_STALE, "pricing", 200, "長文ナレッジ"),
            "content": "あ" * 500,
        }

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=[long_content_item])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.eq.return_value \
            .eq.return_value.contains.return_value \
            .execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.freshness.get_service_client", return_value=mock_db):
            result = await detect_stale_knowledge(COMPANY_ID)

        assert len(result[0]["item"]["content_preview"]) <= 200


class TestFreshnessThresholds:
    def test_pricing_threshold_is_90_days(self):
        assert FRESHNESS_THRESHOLDS["pricing"] == 90

    def test_workflow_threshold_is_365_days(self):
        assert FRESHNESS_THRESHOLDS["workflow"] == 365

    def test_all_thresholds_are_positive(self):
        for key, value in FRESHNESS_THRESHOLDS.items():
            assert value > 0, f"Threshold for {key} must be positive"

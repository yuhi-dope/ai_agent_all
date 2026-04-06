"""BPO Manager ProactiveScanner のユニットテスト"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.manager.proactive_scanner import (
    _auto_execute_proposals,
    _scan_from_knowledge_items,
    scan_proactive_tasks,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------


def _make_proposal(
    impact_score: float = 0.3,
    execution_level: int = 1,
    pipeline: str = "construction/estimation",
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "impact_score": impact_score,
        "execution_level": execution_level,
        "metadata": {
            "pipeline": pipeline,
            "input_data": input_data or {},
        },
    }


# ---------------------------------------------------------------------------
# _auto_execute_proposals
# ---------------------------------------------------------------------------


class TestAutoExecuteProposals:
    """_auto_execute_proposals の条件分岐テスト。"""

    @pytest.mark.asyncio
    async def test_low_impact_low_level_executes(self) -> None:
        """impact_score < 0.5 かつ execution_level <= 1 → 自動実行される。"""
        proposal = _make_proposal(impact_score=0.3, execution_level=1, pipeline="construction/estimation")

        mock_result = MagicMock(success=True)
        with patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            executed = await _auto_execute_proposals("company-1", [proposal])

        assert "construction/estimation" in executed

    @pytest.mark.asyncio
    async def test_high_impact_skips_execution(self) -> None:
        """impact_score >= 0.5 → 自動実行されない。"""
        proposal = _make_proposal(impact_score=0.8, execution_level=1, pipeline="construction/estimation")

        executed = await _auto_execute_proposals("company-1", [proposal])

        assert executed == []

    @pytest.mark.asyncio
    async def test_high_execution_level_skips(self) -> None:
        """execution_level > 1 → 自動実行されない。"""
        proposal = _make_proposal(impact_score=0.3, execution_level=2, pipeline="construction/estimation")

        executed = await _auto_execute_proposals("company-1", [proposal])

        assert executed == []

    @pytest.mark.asyncio
    async def test_no_pipeline_key_skips(self) -> None:
        """pipeline が空文字 → 自動実行されない。"""
        proposal = {
            "impact_score": 0.2,
            "execution_level": 0,
            "metadata": {"pipeline": "", "input_data": {}},
        }

        executed = await _auto_execute_proposals("company-1", [proposal])

        assert executed == []

    @pytest.mark.asyncio
    async def test_execution_error_does_not_raise(self) -> None:
        """route_and_execute が失敗しても例外を上げない。"""
        proposal = _make_proposal(impact_score=0.3, execution_level=1, pipeline="construction/estimation")

        with patch(
            "workers.bpo.manager.task_router.route_and_execute",
            side_effect=Exception("pipeline error"),
        ):
            # 例外が上がらないことを確認
            executed = await _auto_execute_proposals("company-1", [proposal])

        assert executed == []

    @pytest.mark.asyncio
    async def test_multiple_proposals_only_eligible_executed(self) -> None:
        """複数提案のうち条件を満たすものだけ実行される。"""
        proposals = [
            _make_proposal(impact_score=0.2, execution_level=0, pipeline="construction/estimation"),
            _make_proposal(impact_score=0.9, execution_level=1, pipeline="manufacturing/quoting"),
            _make_proposal(impact_score=0.3, execution_level=3, pipeline="nursing/care_billing"),
        ]

        mock_result = MagicMock(success=True)
        with patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            executed = await _auto_execute_proposals("company-1", proposals)

        assert "construction/estimation" in executed
        assert "manufacturing/quoting" not in executed
        assert "nursing/care_billing" not in executed

    @pytest.mark.asyncio
    async def test_empty_proposals_returns_empty(self) -> None:
        """提案が空 → 空リストを返す。"""
        executed = await _auto_execute_proposals("company-1", [])
        assert executed == []


# ---------------------------------------------------------------------------
# _scan_from_knowledge_items (フォールバック)
# ---------------------------------------------------------------------------


class TestScanFromKnowledgeItems:
    """_scan_from_knowledge_items のテスト。"""

    @pytest.mark.asyncio
    async def test_returns_tasks_with_proactive_trigger(self) -> None:
        """trigger_type=proactive かつ condition_met=True のアイテムをタスク化する。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "item-1",
                    "title": "積算アラート",
                    "metadata": {
                        "trigger_type": "proactive",
                        "pipeline": "construction/estimation",
                        "condition_met": True,
                        "input_data": {"project_id": "proj-1"},
                    },
                    "confidence": 0.8,
                }
            ]
        )

        with patch("db.supabase.get_service_client", return_value=mock_db):
            tasks = await _scan_from_knowledge_items("company-1")

        assert len(tasks) == 1
        assert tasks[0].pipeline == "construction/estimation"

    @pytest.mark.asyncio
    async def test_skips_items_without_pipeline(self) -> None:
        """pipeline が空のアイテムはスキップする。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "item-2",
                    "title": "情報なし",
                    "metadata": {
                        "trigger_type": "proactive",
                        "pipeline": "",
                        "condition_met": True,
                    },
                    "confidence": 0.7,
                }
            ]
        )

        with patch("db.supabase.get_service_client", return_value=mock_db):
            tasks = await _scan_from_knowledge_items("company-1")

        assert tasks == []

    @pytest.mark.asyncio
    async def test_skips_items_condition_not_met(self) -> None:
        """condition_met=False のアイテムはスキップする。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {
                    "id": "item-3",
                    "title": "条件未成立",
                    "metadata": {
                        "trigger_type": "proactive",
                        "pipeline": "construction/estimation",
                        "condition_met": False,
                    },
                    "confidence": 0.7,
                }
            ]
        )

        with patch("db.supabase.get_service_client", return_value=mock_db):
            tasks = await _scan_from_knowledge_items("company-1")

        assert tasks == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self) -> None:
        """DBエラー時は空リストを返す。"""
        mock_db = MagicMock()
        mock_db.table.side_effect = Exception("connection error")

        with patch("db.supabase.get_service_client", return_value=mock_db):
            tasks = await _scan_from_knowledge_items("company-1")

        assert tasks == []


# ---------------------------------------------------------------------------
# scan_proactive_tasks (メインエントリ)
# ---------------------------------------------------------------------------


class TestScanProactiveTasks:
    """scan_proactive_tasks のテスト。"""

    @pytest.mark.asyncio
    async def test_falls_back_to_knowledge_items_on_import_error(self) -> None:
        """brain.proactive.analyzer が存在しない場合はknowledge_itemsフォールバック。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        with patch("db.supabase.get_service_client", return_value=mock_db):
            tasks = await scan_proactive_tasks("company-1")

        # brain.proactive.analyzerがないため knowledge_items フォールバックが走る
        assert isinstance(tasks, list)

    @pytest.mark.asyncio
    async def test_returns_empty_on_unexpected_exception(self) -> None:
        """analyze_and_propose が非ImportError例外 → 空リストを返す。"""
        mock_db = MagicMock()
        mock_analyze = AsyncMock(side_effect=RuntimeError("unexpected error"))

        with (
            patch("db.supabase.get_service_client", return_value=mock_db),
            patch("brain.proactive.analyzer.analyze_and_propose", mock_analyze, create=True),
        ):
            tasks = await scan_proactive_tasks("company-1")

        assert tasks == []

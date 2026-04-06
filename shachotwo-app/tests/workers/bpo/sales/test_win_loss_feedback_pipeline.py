"""学習パイプライン⑧ 受注/失注フィードバック テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
    run_win_loss_feedback_pipeline,
    WinLossFeedbackResult,
)

COMPANY_ID = "test-company-001"


def _make_mock_llm(return_value: str) -> MagicMock:
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value=return_value)
    return mock_llm


class TestWinLossFeedbackPipeline:
    """受注/失注フィードバックパイプラインの基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_win_mode_returns_result(self):
        """受注モードでWinLossFeedbackResultを返すことを確認。"""
        mock_llm = _make_mock_llm('{"patterns": [], "weight_adjustments": {}}')
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "1"}])
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

        with patch(
            "workers.bpo.sales.learning.win_loss_feedback_pipeline.get_llm_client",
            return_value=mock_llm,
        ), patch(
            "workers.bpo.sales.learning.win_loss_feedback_pipeline.get_service_client",
            return_value=mock_db,
        ):
            result = await run_win_loss_feedback_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "outcome": "won",
                    "opportunity_id": "opp-001",
                    "company_name": "テスト建設株式会社",
                    "industry": "construction",
                    "employee_range": "50-99",
                    "lead_source": "outreach",
                    "sales_cycle_days": 21,
                    "selected_modules": ["estimation", "safety_docs"],
                },
            )
            assert isinstance(result, WinLossFeedbackResult)

    @pytest.mark.asyncio
    async def test_loss_mode_returns_result(self):
        """失注モードでWinLossFeedbackResultを返すことを確認。"""
        mock_llm = _make_mock_llm('{"patterns": [], "follow_up_email": ""}')
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "1"}])
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])

        with patch(
            "workers.bpo.sales.learning.win_loss_feedback_pipeline.get_llm_client",
            return_value=mock_llm,
        ), patch(
            "workers.bpo.sales.learning.win_loss_feedback_pipeline.get_service_client",
            return_value=mock_db,
        ):
            result = await run_win_loss_feedback_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "outcome": "lost",
                    "opportunity_id": "opp-002",
                    "company_name": "テスト工務店",
                    "industry": "construction",
                    "employee_range": "10-29",
                    "lost_reason": "price",
                },
            )
            assert isinstance(result, WinLossFeedbackResult)

"""Tests for brain/proactive/resolution.py — Human-in-the-Loop 確認・解決フロー。"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.proactive.resolution import (
    accept_proposal,
    get_pending_proposals,
    reject_proposal,
    review_proposal,
)


COMPANY_ID = str(uuid4())
PROPOSAL_ID = str(uuid4())
REVIEWER_ID = str(uuid4())
_KID_A = str(uuid4())
_KID_B = str(uuid4())


def _make_proposal(
    proposal_type: str = "rule_challenge",
    knowledge_ids: list[str] | None = None,
) -> dict:
    return {
        "id": PROPOSAL_ID,
        "company_id": COMPANY_ID,
        "proposal_type": proposal_type,
        "title": "テスト提案",
        "description": "テスト説明",
        "status": "proposed",
        "related_knowledge_ids": knowledge_ids or [_KID_A, _KID_B],
    }


class TestReviewProposal:
    @pytest.mark.asyncio
    async def test_updates_status_to_reviewed(self):
        mock_db = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await review_proposal(COMPANY_ID, PROPOSAL_ID, REVIEWER_ID)

        assert result["status"] == "reviewed"
        assert result["proposal_id"] == PROPOSAL_ID

        # updateに正しいデータが渡されること
        mock_db.table.return_value.update.assert_called_once_with(
            {"status": "reviewed", "reviewed_by": REVIEWER_ID}
        )


class TestAcceptProposal:
    @pytest.mark.asyncio
    async def test_accept_without_action_returns_accepted(self):
        """resolution_actionなしで承認した場合はacceptedで止まること。"""
        mock_db = MagicMock()
        proposal = _make_proposal()

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=proposal)

        mock_db.table.return_value.update.return_value \
            .eq.return_value.execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(COMPANY_ID, PROPOSAL_ID, REVIEWER_ID)

        assert result["status"] == "accepted"
        assert result["proposal_id"] == PROPOSAL_ID

    @pytest.mark.asyncio
    async def test_deactivate_a_implements_proposal(self):
        """deactivate_a アクションでknowledge[0]が無効化されimplementedになること。"""
        mock_db = MagicMock()
        proposal = _make_proposal(knowledge_ids=[_KID_A, _KID_B])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=proposal)

        mock_db.table.return_value.update.return_value \
            .eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(
                COMPANY_ID, PROPOSAL_ID, REVIEWER_ID, resolution_action="deactivate_a"
            )

        assert result["status"] == "implemented"
        assert result["action"] == "deactivate_a"

    @pytest.mark.asyncio
    async def test_deactivate_b_implements_proposal(self):
        """deactivate_b アクションでknowledge[1]が無効化されimplementedになること。"""
        mock_db = MagicMock()
        proposal = _make_proposal(knowledge_ids=[_KID_A, _KID_B])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=proposal)

        mock_db.table.return_value.update.return_value \
            .eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(
                COMPANY_ID, PROPOSAL_ID, REVIEWER_ID, resolution_action="deactivate_b"
            )

        assert result["status"] == "implemented"
        assert result["action"] == "deactivate_b"

    @pytest.mark.asyncio
    async def test_refresh_action_implements_proposal(self):
        """refresh アクションでupdated_atが更新されimplementedになること。"""
        mock_db = MagicMock()
        proposal = _make_proposal(knowledge_ids=[_KID_A])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=proposal)

        mock_db.table.return_value.update.return_value \
            .eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(
                COMPANY_ID, PROPOSAL_ID, REVIEWER_ID, resolution_action="refresh"
            )

        assert result["status"] == "implemented"
        assert result["action"] == "refresh"

    @pytest.mark.asyncio
    async def test_proposal_not_found_returns_error(self):
        """提案が存在しない場合はerrorを返すこと。"""
        mock_db = MagicMock()

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=None)

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(COMPANY_ID, PROPOSAL_ID, REVIEWER_ID)

        assert "error" in result
        assert result["error"] == "proposal_not_found"

    @pytest.mark.asyncio
    async def test_unknown_action_returns_accepted_not_implemented(self):
        """未知のresolution_actionはimplementedにならずacceptedになること。"""
        mock_db = MagicMock()
        proposal = _make_proposal(knowledge_ids=[_KID_A, _KID_B])

        mock_db.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=proposal)

        mock_db.table.return_value.update.return_value \
            .eq.return_value.execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await accept_proposal(
                COMPANY_ID, PROPOSAL_ID, REVIEWER_ID, resolution_action="unknown_action"
            )

        assert result["status"] == "accepted"


class TestRejectProposal:
    @pytest.mark.asyncio
    async def test_updates_status_to_rejected(self):
        mock_db = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await reject_proposal(
                COMPANY_ID, PROPOSAL_ID, REVIEWER_ID, reason="内容が不正確"
            )

        assert result["status"] == "rejected"
        assert result["proposal_id"] == PROPOSAL_ID

        mock_db.table.return_value.update.assert_called_once_with(
            {"status": "rejected", "reviewed_by": REVIEWER_ID}
        )

    @pytest.mark.asyncio
    async def test_reject_without_reason(self):
        """reason省略でも正常に動作すること。"""
        mock_db = MagicMock()
        mock_db.table.return_value.update.return_value \
            .eq.return_value.eq.return_value \
            .execute.return_value = MagicMock()

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await reject_proposal(COMPANY_ID, PROPOSAL_ID, REVIEWER_ID)

        assert result["status"] == "rejected"


class TestGetPendingProposals:
    @pytest.mark.asyncio
    async def test_returns_proposed_and_reviewed(self):
        """proposed と reviewed のステータスのみ取得すること。"""
        mock_proposals = [
            {**_make_proposal(), "status": "proposed"},
            {**_make_proposal(), "id": str(uuid4()), "status": "reviewed"},
        ]

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.in_.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=mock_proposals)

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await get_pending_proposals(COMPANY_ID)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_filters_by_proposal_type(self):
        """proposal_typeフィルタが渡された場合にクエリに適用されること。"""
        mock_db = MagicMock()

        # proposal_type filter chain
        type_chain = mock_db.table.return_value.select.return_value \
            .eq.return_value.in_.return_value \
            .order.return_value.limit.return_value \
            .eq.return_value
        type_chain.execute.return_value = MagicMock(data=[])

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await get_pending_proposals(COMPANY_ID, proposal_type="rule_challenge")

        assert result == []

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value \
            .eq.return_value.in_.return_value \
            .order.return_value.limit.return_value \
            .execute.return_value = MagicMock(data=None)

        with patch("brain.proactive.resolution.get_service_client", return_value=mock_db):
            result = await get_pending_proposals(COMPANY_ID)

        assert result == []

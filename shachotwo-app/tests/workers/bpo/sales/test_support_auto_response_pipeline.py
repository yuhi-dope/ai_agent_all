"""CS パイプライン⑥ サポート自動対応 テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.cs.support_auto_response_pipeline import (
    run_support_auto_response_pipeline,
    SupportAutoResponseResult,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = "test-company-001"


def _make_micro_out(agent_name: str, data: dict) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=data,
        confidence=0.9,
        cost_yen=0.0,
        duration_ms=0,
    )


class TestSupportAutoResponsePipeline:
    """サポート自動対応パイプラインの基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_pipeline_returns_result(self):
        """パイプラインがSupportAutoResponseResultを返すことを確認。"""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"category": "general", "urgency": "low", "confidence": 0.85, "response": "テスト回答", "escalate": false}'
        )

        reader_out = _make_micro_out(
            "saas_reader",
            {"data": [], "count": 0, "service": "supabase"},
        )
        writer_out = _make_micro_out("saas_writer", {"success": True})
        extractor_out = _make_micro_out(
            "extractor",
            {
                "category": "general",
                "urgency": "low",
                "confidence": 0.85,
                "response": "テスト回答",
                "escalate": False,
            },
        )
        validator_out = _make_micro_out("validator", {"valid": True})

        with patch(
            "workers.bpo.sales.cs.support_auto_response_pipeline.get_llm_client",
            return_value=mock_llm,
        ), patch(
            "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=reader_out,
        ), patch(
            "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ), patch(
            "workers.bpo.sales.cs.support_auto_response_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=extractor_out,
        ), patch(
            "workers.bpo.sales.cs.support_auto_response_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=validator_out,
        ):
            result = await run_support_auto_response_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "ticket_text": "テスト問い合わせ本文",
                    "ticket_subject": "テスト問い合わせ",
                    "channel": "email",
                },
                dry_run=True,
            )
            assert isinstance(result, SupportAutoResponseResult)

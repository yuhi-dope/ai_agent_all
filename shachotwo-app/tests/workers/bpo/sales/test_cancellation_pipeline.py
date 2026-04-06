"""CRM パイプライン⑩ 解約フロー テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.cs.cancellation_pipeline import (
    run_cancellation_pipeline,
    CancellationPipelineResult,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = "test-company-001"
CUSTOMER_ID = "cust-cancel-001"


def _make_saas_out(agent_name: str, data: dict) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=data,
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )


@pytest.fixture()
def mock_micro_agents():
    """run_saas_reader / run_saas_writer / run_message_drafter をモック。"""
    reader_out = _make_saas_out(
        "saas_reader",
        {"data": [{"id": CUSTOMER_ID, "company_id": COMPANY_ID, "status": "active"}]},
    )
    writer_out = _make_saas_out("saas_writer", {"success": True})
    message_out = _make_saas_out("message_drafter", {"subject": "テスト", "body": "テスト本文"})

    with patch(
        "workers.bpo.sales.cs.cancellation_pipeline.run_saas_reader",
        new_callable=AsyncMock,
        return_value=reader_out,
    ), patch(
        "workers.bpo.sales.cs.cancellation_pipeline.run_saas_writer",
        new_callable=AsyncMock,
        return_value=writer_out,
    ), patch(
        "workers.bpo.sales.cs.cancellation_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=message_out,
    ), patch(
        "workers.bpo.sales.cs.cancellation_pipeline.run_output_validator",
        new_callable=AsyncMock,
        return_value=_make_saas_out("validator", {"valid": True}),
    ):
        yield


class TestCancellationPipeline:
    """解約フローパイプラインの基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_pipeline_returns_result(self, mock_micro_agents):
        """パイプラインがCancellationPipelineResultを返すことを確認。"""
        result = await run_cancellation_pipeline(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            input_data={
                "reason_text": "サービスを使わなくなった",
                "contract_id": "contract-001",
                "monthly_amount": 250000,
                "contact_email": "test@example.com",
                "customer_name": "テスト顧客",
                "plan_name": "BPOコアプラン",
            },
        )
        assert isinstance(result, CancellationPipelineResult)

    @pytest.mark.asyncio
    async def test_pipeline_success(self, mock_micro_agents):
        """正常系で success=True になることを確認。"""
        result = await run_cancellation_pipeline(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            input_data={
                "reason_text": "コスト削減",
                "contract_id": "contract-002",
                "monthly_amount": 300000,
                "contact_email": "test2@example.com",
                "customer_name": "テスト顧客2",
                "plan_name": "ブレインプラン",
            },
        )
        assert result.success is True
        assert len(result.steps) > 0

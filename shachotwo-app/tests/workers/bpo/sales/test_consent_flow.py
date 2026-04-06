"""SFA パイプライン③b 電子同意フロー テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.sfa.consent_flow import (
    run_consent_flow_pipeline,
    process_consent_agreement,
    ConsentFlowResult,
    _generate_consent_token,
    _build_consent_url,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = "test-company-001"
CONTRACT_ID = "contract-001"


def _make_pdf_out() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="pdf_generator",
        success=True,
        result={
            "pdf_bytes": b"%PDF-1.4 dummy",
            "filename": "contract.pdf",
            "page_count": 1,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )


class TestConsentFlowHelpers:
    """ヘルパー関数のテスト。"""

    def test_generate_consent_token_is_uuid(self):
        """同意トークンがUUID形式であることを確認。"""
        token = _generate_consent_token()
        assert len(token) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
        assert token.count("-") == 4

    def test_build_consent_url(self):
        """同意URLが正しく生成されることを確認。"""
        token = "test-token-123"
        url = _build_consent_url(token)
        assert token in url
        assert "/sales/consent/" in url


@pytest.fixture()
def mock_supabase():
    with patch("workers.bpo.sales.sfa.consent_flow.get_service_client") as m:
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "1"}])
        client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        client.storage.from_.return_value.upload.return_value = None
        client.storage.from_.return_value.get_public_url.return_value = "https://example.com/signed.pdf"
        m.return_value = client
        yield client


@pytest.fixture()
def mock_pdf_generator():
    """pdf_generator マイクロエージェントをモック。"""
    with patch(
        "workers.bpo.sales.sfa.consent_flow._generate_contract_pdf",
        new_callable=AsyncMock,
        return_value={
            "pdf_bytes": b"%PDF-1.4 dummy",
            "filename": "contract.pdf",
            "page_count": 1,
            "cost_yen": 0.0,
            "duration_ms": 0,
        },
    ), patch(
        "workers.bpo.sales.sfa.consent_flow._generate_stamped_pdf",
        new_callable=AsyncMock,
        return_value={
            "pdf_bytes": b"%PDF-1.4 stamped",
            "filename": "contract_signed.pdf",
            "cost_yen": 0.0,
            "duration_ms": 0,
        },
    ):
        yield


class TestConsentFlowPipeline:
    """同意依頼フロー（Step 1-3）のテスト。"""

    @pytest.mark.asyncio
    async def test_consent_flow_returns_result(self, mock_supabase, mock_pdf_generator):
        """パイプラインがConsentFlowResultを返すことを確認。"""
        result = await run_consent_flow_pipeline(
            company_id=COMPANY_ID,
            contract_id=CONTRACT_ID,
            contract_data={"contract_title": "テスト契約書", "amount": 250000},
            to_email="customer@example.com",
        )
        assert isinstance(result, ConsentFlowResult)
        assert result.consent_token is not None
        assert result.consent_url is not None

    @pytest.mark.asyncio
    async def test_consent_flow_contains_steps(self, mock_supabase, mock_pdf_generator):
        """3ステップ（PDF生成+トークン生成+メール送信）が実行されることを確認。"""
        result = await run_consent_flow_pipeline(
            company_id=COMPANY_ID,
            contract_id=CONTRACT_ID,
            contract_data={"contract_title": "テスト契約書"},
            to_email="customer@example.com",
        )
        assert len(result.steps) == 3


class TestProcessConsentAgreement:
    """同意実行後フロー（Step 4-7）のテスト。"""

    @pytest.mark.asyncio
    async def test_agreement_returns_result(self, mock_supabase, mock_pdf_generator):
        """同意処理がConsentFlowResultを返すことを確認。"""
        result = await process_consent_agreement(
            company_id=COMPANY_ID,
            contract_id=CONTRACT_ID,
            consent_token="test-token-123",
            user_id="user-001",
            ip_address="192.168.1.1",
            user_agent="TestBrowser/1.0",
            contract_data={"contract_title": "テスト契約書"},
        )
        assert isinstance(result, ConsentFlowResult)

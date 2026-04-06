"""共通マイクロエージェント P1 テスト（残り8体）。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.models import MicroAgentInput
from workers.micro.table_parser import run_table_parser
from workers.micro.calculator import run_cost_calculator
from workers.micro.compliance import run_compliance_checker
from workers.micro.diff import run_diff_detector
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer

COMPANY_ID = "test-company-001"


# ─── table_parser ────────────────────────────────────────────────────────────

class TestTableParser:
    @pytest.mark.asyncio
    async def test_pipe_table(self):
        text = "工種|数量|単位\n掘削|100|m3\n舗装|500|m2"
        out = await run_table_parser(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="table_parser",
            payload={"text": text, "format": "pipe"},
        ))
        assert out.success is True
        assert out.result["count"] == 1
        assert out.result["tables"][0][0]["工種"] == "掘削"

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self):
        out = await run_table_parser(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="table_parser",
            payload={"text": "", "format": "auto"},
        ))
        assert out.success is True
        assert out.result["count"] == 0

    @pytest.mark.asyncio
    async def test_markdown_table(self):
        text = "| 名前 | 金額 |\n|---|---|\n| テスト | 1000 |"
        out = await run_table_parser(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="table_parser",
            payload={"text": text, "format": "markdown"},
        ))
        assert out.success is True
        assert out.result["tables"][0][0]["名前"] == "テスト"


# ─── cost_calculator ─────────────────────────────────────────────────────────

class TestCostCalculator:
    @pytest.mark.asyncio
    async def test_basic_calculation(self):
        items = [
            {"category": "土工", "quantity": 100, "unit_price": 1500},
            {"category": "舗装工", "quantity": 200, "unit_price": 3000},
        ]
        out = await run_cost_calculator(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="cost_calculator",
            payload={"items": items},
        ))
        assert out.success is True
        assert out.result["subtotal"] == 100 * 1500 + 200 * 3000
        assert out.result["zero_price_count"] == 0
        assert out.confidence == 1.0

    @pytest.mark.asyncio
    async def test_zero_price_lowers_confidence(self):
        items = [
            {"category": "土工", "quantity": 100, "unit_price": 1500},
            {"category": "不明工", "quantity": 50, "unit_price": None},
        ]
        out = await run_cost_calculator(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="cost_calculator",
            payload={"items": items},
        ))
        assert out.result["zero_price_count"] == 1
        assert out.confidence == 0.5

    @pytest.mark.asyncio
    async def test_decimal_precision(self):
        """小数点を含む計算がDecimalで正確に行われる"""
        items = [{"quantity": 1.5, "unit_price": 1234.56}]
        out = await run_cost_calculator(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="cost_calculator",
            payload={"items": items},
        ))
        assert out.result["subtotal"] == int(1.5 * 1234.56)


# ─── compliance_checker ──────────────────────────────────────────────────────

class TestComplianceChecker:
    @pytest.mark.asyncio
    async def test_construction_subcontract_limit_violation(self):
        """下請け4500万超はエラー"""
        out = await run_compliance_checker(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="compliance_checker",
            payload={
                "data": {"subcontract_total": 50_000_000, "has_special_construction_license": False},
                "industry": "construction",
            },
        ))
        assert out.success is True
        assert out.result["passed"] is False
        assert any(v["rule_id"] == "const_001" for v in out.result["violations"])

    @pytest.mark.asyncio
    async def test_construction_with_license_passes(self):
        """特定建設業許可あれば通過"""
        out = await run_compliance_checker(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="compliance_checker",
            payload={
                "data": {"subcontract_total": 50_000_000, "has_special_construction_license": True},
                "industry": "construction",
            },
        ))
        errors = [v for v in out.result["violations"] if v["severity"] == "error"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_confidence_is_always_1(self):
        """ルールベースなのでconfidence=1.0固定"""
        out = await run_compliance_checker(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="compliance_checker",
            payload={"data": {}, "industry": "common"},
        ))
        assert out.confidence == 1.0


# ─── diff_detector ───────────────────────────────────────────────────────────

class TestDiffDetector:
    @pytest.mark.asyncio
    async def test_detects_changes(self):
        out = await run_diff_detector(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="diff_detector",
            payload={
                "before": {"total": 100000, "status": "draft"},
                "after": {"total": 120000, "status": "approved"},
            },
        ))
        assert out.success is True
        assert out.result["change_count"] == 2
        assert out.result["significant"] is True  # total はsignificantフィールド

    @pytest.mark.asyncio
    async def test_no_change(self):
        data = {"total": 100000}
        out = await run_diff_detector(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="diff_detector",
            payload={"before": data, "after": data},
        ))
        assert out.result["change_count"] == 0
        assert out.result["significant"] is False

    @pytest.mark.asyncio
    async def test_change_rate_calculation(self):
        out = await run_diff_detector(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="diff_detector",
            payload={
                "before": {"amount": 100000},
                "after": {"amount": 110000},
            },
        ))
        change = out.result["changes"][0]
        assert change["change_rate"] == pytest.approx(0.1, abs=0.001)


# ─── saas_reader ─────────────────────────────────────────────────────────────

class TestSaasReader:
    @pytest.mark.asyncio
    async def test_returns_mock_for_freee(self):
        out = await run_saas_reader(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="saas_reader",
            payload={"service": "freee", "operation": "list_expenses", "params": {}},
        ))
        assert out.success is True
        assert out.result["mock"] is True
        assert out.result["service"] == "freee"

    @pytest.mark.asyncio
    async def test_missing_service_returns_error(self):
        out = await run_saas_reader(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="saas_reader",
            payload={"service": "", "operation": "list", "params": {}},
        ))
        assert out.success is False


# ─── saas_writer ─────────────────────────────────────────────────────────────

class TestSaasWriter:
    @pytest.mark.asyncio
    async def test_unapproved_write_is_rejected(self):
        """approved=Falseは必ず拒否"""
        out = await run_saas_writer(MicroAgentInput(
            company_id=COMPANY_ID, agent_name="saas_writer",
            payload={
                "service": "freee", "operation": "create_expense",
                "params": {"amount": 5000}, "approved": False,
            },
        ))
        assert out.success is False
        assert out.result.get("requires_approval") is True

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self):
        """dry_run=TrueはDBに書かずにoperation_idを返す"""
        with patch("workers.micro.saas_writer._log_to_audit", new_callable=AsyncMock, return_value="audit-001"):
            out = await run_saas_writer(MicroAgentInput(
                company_id=COMPANY_ID, agent_name="saas_writer",
                payload={
                    "service": "freee", "operation": "create_expense",
                    "params": {}, "approved": True, "dry_run": True,
                },
            ))
        assert out.success is True
        assert out.result["dry_run"] is True
        assert out.result["operation_id"] is not None

    @pytest.mark.asyncio
    async def test_connector_write_freee(self):
        """freee への書き込みがコネクタ経由で実行される"""
        mock_connector = AsyncMock()
        mock_connector.write_record = AsyncMock(return_value={"id": "exp-999", "status": "created"})

        with patch("workers.micro.saas_writer._log_to_audit", new_callable=AsyncMock, return_value="audit-002"), \
             patch("workers.micro.saas_writer._fetch_encrypted_credentials", new_callable=AsyncMock, return_value="enc_creds_dummy"), \
             patch("workers.micro.saas_writer.get_connector", return_value=mock_connector, create=True):
            # get_connector はモジュールスコープでは未インポートのため動的パッチ
            with patch("workers.connector.factory.get_connector", return_value=mock_connector):
                out = await run_saas_writer(MicroAgentInput(
                    company_id=COMPANY_ID, agent_name="saas_writer",
                    payload={
                        "service": "freee",
                        "operation": "create_expense",
                        "params": {"resource": "expenses", "data": {"amount": 10000, "description": "交通費"}},
                        "approved": True,
                    },
                ))

        assert out.success is True
        assert out.result["dry_run"] is False
        assert out.result.get("mock") is not True

    @pytest.mark.asyncio
    async def test_connector_write_no_credentials_returns_mock(self):
        """クレデンシャル未登録の場合は mock=True で graceful degradation"""
        with patch("workers.micro.saas_writer._log_to_audit", new_callable=AsyncMock, return_value="audit-003"), \
             patch("workers.micro.saas_writer._fetch_encrypted_credentials", new_callable=AsyncMock, return_value=None):
            out = await run_saas_writer(MicroAgentInput(
                company_id=COMPANY_ID, agent_name="saas_writer",
                payload={
                    "service": "kintone",
                    "operation": "add_record",
                    "params": {"resource": "app_123", "data": {"title": "テスト"}},
                    "approved": True,
                },
            ))

        assert out.success is True
        assert out.result["mock"] is True
        assert "クレデンシャル未設定" in out.result["reason"]
        assert out.confidence == 0.5

    @pytest.mark.asyncio
    async def test_connector_write_slack(self):
        """slack への書き込みがコネクタ経由で実行される"""
        mock_connector = AsyncMock()
        mock_connector.write_record = AsyncMock(return_value={"ok": True, "ts": "1234567890.000001"})

        with patch("workers.micro.saas_writer._log_to_audit", new_callable=AsyncMock, return_value="audit-004"), \
             patch("workers.micro.saas_writer._fetch_encrypted_credentials", new_callable=AsyncMock, return_value="enc_slack_creds"), \
             patch("workers.connector.factory.get_connector", return_value=mock_connector):
            out = await run_saas_writer(MicroAgentInput(
                company_id=COMPANY_ID, agent_name="saas_writer",
                payload={
                    "service": "slack",
                    "operation": "post_message",
                    "params": {"resource": "channels/general", "data": {"text": "テスト通知"}},
                    "approved": True,
                },
            ))

        assert out.success is True
        assert out.result["dry_run"] is False

    @pytest.mark.asyncio
    async def test_unknown_service_returns_success_with_noop(self):
        """未対応サービスは noop で success=True（既存動作維持）"""
        with patch("workers.micro.saas_writer._log_to_audit", new_callable=AsyncMock, return_value="audit-005"):
            out = await run_saas_writer(MicroAgentInput(
                company_id=COMPANY_ID, agent_name="saas_writer",
                payload={
                    "service": "unknown_erp",
                    "operation": "sync_data",
                    "params": {},
                    "approved": True,
                },
            ))

        assert out.success is True
        assert out.result.get("mock") is not True

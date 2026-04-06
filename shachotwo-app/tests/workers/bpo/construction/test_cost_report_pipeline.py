"""建設業 月次原価報告パイプライン テスト。"""
import pytest
from unittest.mock import AsyncMock, patch

from workers.bpo.construction.pipelines.cost_report_pipeline import (
    run_cost_report_pipeline,
    CostReportPipelineResult,
    CONFIDENCE_WARNING_THRESHOLD,
)
from workers.micro.models import MicroAgentOutput


COMPANY_ID = "test-company-001"

SAMPLE_COST_RECORDS = [
    {"cost_type": "労務費", "amount": 500000, "description": "作業員賃金"},
    {"cost_type": "材料費", "amount": 300000, "description": "コンクリート材料"},
    {"cost_type": "外注費", "amount": 200000, "description": "型枠工事"},
]

MOCK_REPORT_OUTPUT = MicroAgentOutput(
    agent_name="document_generator", success=True,
    result={"content": "# 月次原価報告書\n\n報告内容", "format": "markdown", "char_count": 20},
    confidence=0.9, cost_yen=1.5, duration_ms=300,
)


class TestCostReportPipelineHappyPath:
    """正常系テスト"""

    @pytest.mark.asyncio
    async def test_direct_cost_records_completes_successfully(self):
        """直接cost_records渡しで正常完了し、4ステップが全て実行される"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result: CostReportPipelineResult = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": SAMPLE_COST_RECORDS,
                    "contract_amount": 1500000,
                },
                period_year=2026,
                period_month=3,
            )

        assert result.success is True
        assert result.failed_step is None
        assert result.total_duration_ms >= 0
        assert result.final_output != {}

    @pytest.mark.asyncio
    async def test_all_four_steps_are_executed(self):
        """全4ステップが実行される"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": SAMPLE_COST_RECORDS,
                    "contract_amount": 1500000,
                },
            )

        assert len(result.steps) == 4
        step_names = [s.step_name for s in result.steps]
        assert step_names[0] == "cost_reader"
        assert step_names[1] == "variance_calculator"
        assert step_names[2] == "risk_detector"
        assert step_names[3] == "report_generator"

    @pytest.mark.asyncio
    async def test_profit_rate_is_correctly_calculated(self):
        """profit_rate が正しく計算される（contract_amount=1,000,000、total=700,000→30%）"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "労務費", "amount": 400000, "description": "賃金"},
                        {"cost_type": "材料費", "amount": 300000, "description": "材料"},
                    ],
                    "contract_amount": 1000000,
                },
            )

        assert result.success is True
        # total_actual_cost = 700,000 / contract_amount = 1,000,000 → profit_rate = 0.30
        step2 = result.steps[1]
        assert step2.step_name == "variance_calculator"
        assert abs(step2.result["profit_rate"] - 0.30) < 0.001
        assert step2.result["profit"] == 300000
        assert step2.result["total_actual_cost"] == 700000

    @pytest.mark.asyncio
    async def test_cost_by_type_aggregation(self):
        """同一工種のコストが正しく集計される"""
        records = [
            {"cost_type": "労務費", "amount": 100000, "description": "week1"},
            {"cost_type": "労務費", "amount": 200000, "description": "week2"},
            {"cost_type": "材料費", "amount": 150000, "description": "材料"},
        ]
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={"cost_records": records, "contract_amount": 1000000},
            )

        step2 = result.steps[1]
        cost_by_type = step2.result["cost_by_type"]
        assert cost_by_type["労務費"] == 300000
        assert cost_by_type["材料費"] == 150000

    @pytest.mark.asyncio
    async def test_period_is_passed_to_report(self):
        """period_year / period_month が final_output の period フィールドに反映される"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": SAMPLE_COST_RECORDS,
                    "contract_amount": 2000000,
                },
                period_year=2026,
                period_month=3,
            )

        assert result.final_output["period"] == "2026年3月"


class TestCostReportPipelineRiskDetection:
    """リスク検出テスト"""

    @pytest.mark.asyncio
    async def test_deficit_generates_risk_alert(self):
        """赤字時にrisk_alertsにアラートが入る"""
        # contract_amount=500,000 に対してコスト=800,000（赤字）
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "労務費", "amount": 500000, "description": ""},
                        {"cost_type": "材料費", "amount": 300000, "description": ""},
                    ],
                    "contract_amount": 500000,
                },
            )

        assert len(result.risk_alerts) >= 1
        assert any("赤字" in alert for alert in result.risk_alerts)

    @pytest.mark.asyncio
    async def test_low_profit_rate_generates_warning(self):
        """利益率 < 5% で低収益警告が入る"""
        # contract_amount=1,000,000 に対してコスト=970,000（利益率3%）
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "労務費", "amount": 970000, "description": ""},
                    ],
                    "contract_amount": 1000000,
                },
            )

        assert any("低収益警告" in alert for alert in result.risk_alerts)

    @pytest.mark.asyncio
    async def test_cost_overrun_alert_when_cost_ratio_exceeds_progress(self):
        """進捗率より10%以上コスト消化が多い場合にコスト超過アラートが入る"""
        # contract=1,000,000、actual=600,000（消化率60%）、進捗40% → 超過
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "労務費", "amount": 600000, "description": ""},
                    ],
                    "contract_amount": 1000000,
                    "progress_rate": 0.40,
                },
            )

        assert any("コスト超過" in alert for alert in result.risk_alerts)

    @pytest.mark.asyncio
    async def test_no_risk_alerts_for_healthy_project(self):
        """利益率が高く進捗も正常なプロジェクトはアラートなし"""
        # contract=2,000,000、actual=800,000（消化率40%）、進捗50%
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "労務費", "amount": 800000, "description": ""},
                    ],
                    "contract_amount": 2000000,
                    "progress_rate": 0.50,
                },
            )

        assert result.success is True
        assert result.risk_alerts == []


class TestCostReportPipelineErrors:
    """エラーハンドリングテスト"""

    @pytest.mark.asyncio
    async def test_empty_cost_records_returns_failure(self):
        """cost_records空でも失敗を返す"""
        result = await run_cost_report_pipeline(
            company_id=COMPANY_ID,
            input_data={"cost_records": [], "contract_amount": 1000000},
        )

        assert result.success is False
        assert result.failed_step == "cost_reader"

    @pytest.mark.asyncio
    async def test_missing_both_cost_records_and_contract_id_returns_failure(self):
        """cost_records も contract_id もない場合は失敗"""
        result = await run_cost_report_pipeline(
            company_id=COMPANY_ID,
            input_data={"contract_amount": 1000000},
        )

        assert result.success is False
        assert result.failed_step == "cost_reader"
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_total_cost_is_sum_of_steps(self):
        """total_cost_yen は各ステップの cost_yen の合計"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": SAMPLE_COST_RECORDS,
                    "contract_amount": 1500000,
                },
            )

        step_total = sum(s.cost_yen for s in result.steps)
        assert abs(result.total_cost_yen - step_total) < 0.001

    @pytest.mark.asyncio
    async def test_risk_alerts_reflected_in_final_output(self):
        """risk_alerts が final_output にも反映される"""
        with patch(
            "workers.bpo.construction.pipelines.cost_report_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_REPORT_OUTPUT),
        ):
            result = await run_cost_report_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "cost_records": [
                        {"cost_type": "外注費", "amount": 900000, "description": ""},
                    ],
                    "contract_amount": 500000,
                },
            )

        assert result.final_output["risk_alerts"] == result.risk_alerts

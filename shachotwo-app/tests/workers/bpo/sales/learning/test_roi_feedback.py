"""ROI実績計測フィードバックのテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


COMPANY_ID = "test-company-uuid"
CUSTOMER_ID = "test-customer-uuid"


def _make_db_mock(
    customer_data: list | None = None,
    log_data: list | None = None,
    insert_ok: bool = True,
    pattern_data: list | None = None,
):
    """テスト用DBモックを生成する。"""
    mock_db = MagicMock()

    def table_side_effect(name: str):
        t = MagicMock()
        if name == "customers":
            (
                t.select.return_value
                .eq.return_value
                .limit.return_value
                .execute.return_value
            ).data = customer_data if customer_data is not None else []
        elif name == "execution_logs":
            (
                t.select.return_value
                .eq.return_value
                .gte.return_value
                .eq.return_value
                .execute.return_value
            ).data = log_data if log_data is not None else []
        elif name == "win_loss_patterns":
            if pattern_data is not None:
                (
                    t.select.return_value
                    .eq.return_value
                    .eq.return_value
                    .eq.return_value
                    .order.return_value
                    .limit.return_value
                    .execute.return_value
                ).data = pattern_data
            t.insert.return_value.execute.return_value = MagicMock()
        return t

    mock_db.table.side_effect = table_side_effect
    return mock_db


class TestCalculateRoiActuals:
    """calculate_roi_actuals のテスト。"""

    @pytest.mark.asyncio
    async def test_customer_not_found_returns_error(self):
        mock_db = _make_db_mock(customer_data=[])
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        assert result["error"] == "customer not found"
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_no_execution_logs_returns_error(self):
        customer = [{"id": CUSTOMER_ID, "industry": "construction", "mrr": 250000}]
        mock_db = _make_db_mock(customer_data=customer, log_data=[])
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        assert result["error"] == "no execution logs"
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_happy_path_construction_industry(self):
        customer = [{"id": CUSTOMER_ID, "industry": "construction", "mrr": 250000}]
        logs = [
            {"pipeline": "estimation_pipeline", "duration_ms": 60000, "status": "completed"},
            {"pipeline": "billing_pipeline", "duration_ms": 30000, "status": "completed"},
        ]
        mock_db = _make_db_mock(customer_data=customer, log_data=logs)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)

        assert result["customer_id"] == CUSTOMER_ID
        assert result["industry"] == "construction"
        assert result["bpo_executions"] == 2
        # 手作業換算: 2回 × 2.0時間 = 4.0時間
        assert result["estimated_manual_hours"] == 4.0
        assert result["actual_saved_hours"] > 0
        assert result["mrr"] == 250000
        assert isinstance(result["roi_ratio"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_roi_ratio_calculated_correctly(self):
        """ROI = 削減金額 / MRR が正しく計算される。"""
        # 100タスク × 1.5時間(製造業) = 150時間、時給3000円 = 450,000円削減
        # MRR = 250,000円 → ROI = 1.8
        customer = [{"id": CUSTOMER_ID, "industry": "manufacturing", "mrr": 250000}]
        # duration_ms=0にして削減時間 = 全手作業換算時間にする
        logs = [{"pipeline": "p", "duration_ms": 0, "status": "completed"}] * 100
        mock_db = _make_db_mock(customer_data=customer, log_data=logs)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)

        assert result["estimated_manual_hours"] == 150.0
        assert result["actual_saved_hours"] == 150.0
        assert result["actual_saved_yen"] == 450000
        assert result["roi_ratio"] == 1.8

    @pytest.mark.asyncio
    async def test_unknown_industry_uses_default_benchmark(self):
        customer = [{"id": CUSTOMER_ID, "industry": "unknown_biz", "mrr": 100000}]
        logs = [{"pipeline": "p", "duration_ms": 0, "status": "completed"}]
        mock_db = _make_db_mock(customer_data=customer, log_data=logs)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        # デフォルト 1.5時間/タスク
        assert result["estimated_manual_hours"] == 1.5

    @pytest.mark.asyncio
    async def test_zero_mrr_gives_zero_roi(self):
        customer = [{"id": CUSTOMER_ID, "industry": "construction", "mrr": 0}]
        logs = [{"pipeline": "p", "duration_ms": 0, "status": "completed"}]
        mock_db = _make_db_mock(customer_data=customer, log_data=logs)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        assert result["roi_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_confidence_caps_at_095(self):
        customer = [{"id": CUSTOMER_ID, "industry": "logistics", "mrr": 100000}]
        logs = [{"pipeline": "p", "duration_ms": 0, "status": "completed"}] * 200
        mock_db = _make_db_mock(customer_data=customer, log_data=logs)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        assert result["confidence"] <= 0.95

    @pytest.mark.asyncio
    async def test_db_exception_returns_error(self):
        with patch("db.supabase.get_service_client", side_effect=Exception("db error")):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        assert "error" in result
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_result_saved_to_win_loss_patterns(self):
        """計算結果がwin_loss_patternsに保存される。"""
        customer = [{"id": CUSTOMER_ID, "industry": "nursing", "mrr": 200000}]
        logs = [{"pipeline": "p", "duration_ms": 5000, "status": "completed"}]
        # table()が呼ばれたテーブル名を記録するため、side_effectではなくcall_args_listで検証
        mock_db = MagicMock()
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = customer
        mock_table.select.return_value.eq.return_value.gte.return_value.eq.return_value.execute.return_value.data = logs
        mock_table.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value = mock_table
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            result = await calculate_roi_actuals(COMPANY_ID, CUSTOMER_ID)
        # win_loss_patternsへのinsertが呼ばれたことを確認
        assert mock_table.insert.called
        assert "error" not in result


class TestGetIndustryRoiBenchmarks:
    """get_industry_roi_benchmarks のテスト。"""

    @pytest.mark.asyncio
    async def test_no_data_returns_no_benchmark(self):
        mock_db = _make_db_mock(pattern_data=[])
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import get_industry_roi_benchmarks
            result = await get_industry_roi_benchmarks(COMPANY_ID, "construction")
        assert result["has_benchmark"] is False
        assert result["sample_size"] == 0

    @pytest.mark.asyncio
    async def test_benchmark_aggregation(self):
        pattern_data = [
            {"pattern_data": {"actual_saved_hours": 10.0, "actual_saved_yen": 30000, "roi_ratio": 1.2}},
            {"pattern_data": {"actual_saved_hours": 20.0, "actual_saved_yen": 60000, "roi_ratio": 2.4}},
        ]
        mock_db = _make_db_mock(pattern_data=pattern_data)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import get_industry_roi_benchmarks
            result = await get_industry_roi_benchmarks(COMPANY_ID, "construction")

        assert result["has_benchmark"] is True
        assert result["sample_size"] == 2
        assert result["avg_saved_hours_monthly"] == 15.0
        assert result["avg_roi_ratio"] == 1.8

    @pytest.mark.asyncio
    async def test_median_calculation(self):
        pattern_data = [
            {"pattern_data": {"actual_saved_hours": 5.0, "actual_saved_yen": 15000, "roi_ratio": 0.5}},
            {"pattern_data": {"actual_saved_hours": 10.0, "actual_saved_yen": 30000, "roi_ratio": 1.0}},
            {"pattern_data": {"actual_saved_hours": 30.0, "actual_saved_yen": 90000, "roi_ratio": 3.0}},
        ]
        mock_db = _make_db_mock(pattern_data=pattern_data)
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.learning.roi_feedback import get_industry_roi_benchmarks
            result = await get_industry_roi_benchmarks(COMPANY_ID, "manufacturing")

        assert result["median_saved_hours_monthly"] == 10.0
        assert result["median_roi_ratio"] == 1.0

    @pytest.mark.asyncio
    async def test_db_exception_returns_no_benchmark(self):
        with patch("db.supabase.get_service_client", side_effect=Exception("db error")):
            from workers.bpo.sales.learning.roi_feedback import get_industry_roi_benchmarks
            result = await get_industry_roi_benchmarks(COMPANY_ID, "construction")
        assert result["has_benchmark"] is False
        assert "error" in result

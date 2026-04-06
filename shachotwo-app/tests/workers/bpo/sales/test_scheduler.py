"""営業BPOスケジューラのテスト。"""
import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


JST = timezone(timedelta(hours=9))


class TestScheduleDefinitions:
    """スケジュール定義の基本的な検証。"""

    def test_schedule_count(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        assert len(SCHEDULES) == 6

    def test_all_schedules_have_required_keys(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        for s in SCHEDULES:
            assert "name" in s
            assert "hour" in s
            assert "minute" in s
            assert "monthly" in s

    def test_monthly_schedules_have_day(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        for s in SCHEDULES:
            if s["monthly"]:
                assert "day" in s

    def test_daily_schedule_names(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        daily_names = {s["name"] for s in SCHEDULES if not s["monthly"]}
        assert "outreach_daily" in daily_names
        assert "health_check_daily" in daily_names
        assert "upsell_check_daily" in daily_names
        assert "outreach_pdca_daily" in daily_names

    def test_monthly_schedule_names(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        monthly_names = {s["name"] for s in SCHEDULES if s["monthly"]}
        assert "revenue_monthly" in monthly_names
        assert "cs_feedback_monthly" in monthly_names

    def test_hours_are_within_range(self):
        from workers.bpo.sales.scheduler import SCHEDULES
        for s in SCHEDULES:
            assert 0 <= s["hour"] <= 23
            assert 0 <= s["minute"] <= 59


class TestGetSystemCompanyId:
    """_get_system_company_id のテスト。"""

    @pytest.mark.asyncio
    async def test_returns_id_from_db(self):
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "shachotwo-company-id"}
        ]
        with patch("db.supabase.get_service_client", return_value=mock_db):
            from workers.bpo.sales.scheduler import _get_system_company_id
            result = await _get_system_company_id()
        assert result == "shachotwo-company-id"

    @pytest.mark.asyncio
    async def test_falls_back_to_env_var(self):
        with patch("db.supabase.get_service_client", side_effect=Exception("no db")):
            with patch.dict("os.environ", {"SYSTEM_COMPANY_ID": "env-company-id"}):
                from workers.bpo.sales.scheduler import _get_system_company_id
                result = await _get_system_company_id()
        assert result == "env-company-id"

    @pytest.mark.asyncio
    async def test_falls_back_to_system_default(self):
        with patch("db.supabase.get_service_client", side_effect=Exception("no db")):
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("SYSTEM_COMPANY_ID", None)
                from workers.bpo.sales.scheduler import _get_system_company_id
                result = await _get_system_company_id()
        assert result == "system"


class TestRunScheduledTask:
    """_run_scheduled_task のタスク振り分けテスト。"""

    @pytest.mark.asyncio
    async def test_outreach_daily_calls_outreach_pipeline(self):
        mock_pipeline = AsyncMock()
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.marketing.outreach_pipeline.run_outreach_pipeline", mock_pipeline):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("outreach_daily")
        mock_pipeline.assert_called_once()
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("company_id") == "cid" or mock_pipeline.call_args.kwargs.get("company_id") == "cid"

    @pytest.mark.asyncio
    async def test_upsell_check_daily_calls_upsell_pipeline(self):
        mock_pipeline = AsyncMock()
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_upsell_briefing_pipeline", mock_pipeline):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("upsell_check_daily")
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_revenue_monthly_calls_revenue_pipeline(self):
        mock_pipeline = AsyncMock()
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.crm.revenue_request_pipeline.run_revenue_request_pipeline", mock_pipeline):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("revenue_monthly")
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_cs_feedback_monthly_calls_cs_feedback_pipeline(self):
        mock_pipeline = AsyncMock()
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.learning.cs_feedback_pipeline.run_cs_feedback_pipeline", mock_pipeline):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("cs_feedback_monthly")
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_outreach_pdca_daily_calls_win_loss_pipeline(self):
        mock_pipeline = AsyncMock()
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.learning.win_loss_feedback_pipeline.run_win_loss_feedback_pipeline", mock_pipeline):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("outreach_pdca_daily")
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_in_pipeline_is_caught(self):
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("workers.bpo.sales.marketing.outreach_pipeline.run_outreach_pipeline", side_effect=Exception("pipeline error")):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            # エラーが上位に伝播しないことを確認
            await _run_scheduled_task("outreach_daily")  # 例外なしで完了すること

    @pytest.mark.asyncio
    async def test_health_check_calls_roi_actuals(self):
        mock_lifecycle = AsyncMock()
        mock_roi = AsyncMock(return_value={"roi_ratio": 2.0, "confidence": 0.8})
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": "cust-1"}, {"id": "cust-2"}
        ]
        with patch("workers.bpo.sales.scheduler._get_system_company_id", new_callable=AsyncMock, return_value="cid"), \
             patch("db.supabase.get_service_client", return_value=mock_db), \
             patch("workers.bpo.sales.crm.customer_lifecycle_pipeline.run_customer_lifecycle_pipeline", mock_lifecycle), \
             patch("workers.bpo.sales.learning.roi_feedback.calculate_roi_actuals", mock_roi):
            from workers.bpo.sales.scheduler import _run_scheduled_task
            await _run_scheduled_task("health_check_daily")
        # ROI計測が2顧客分呼ばれている
        assert mock_roi.call_count == 2


class TestStartStopScheduler:
    """start_scheduler / stop_scheduler のテスト。"""

    @pytest.mark.asyncio
    async def test_start_creates_tasks(self):
        # スケジューラを起動してすぐ停止する
        import workers.bpo.sales.scheduler as sched_module
        sched_module._running = False
        sched_module._tasks.clear()

        # _scheduler_loopが長時間sleepしないようにキャンセルを即時実行
        async def _instant_cancel(schedule):
            return

        with patch.object(sched_module, "_scheduler_loop", side_effect=_instant_cancel):
            await sched_module.start_scheduler()

        assert sched_module._running is True
        await sched_module.stop_scheduler()
        assert sched_module._running is False

    @pytest.mark.asyncio
    async def test_stop_clears_tasks(self):
        import workers.bpo.sales.scheduler as sched_module
        sched_module._running = True
        # ダミータスクを追加
        dummy_task = asyncio.create_task(asyncio.sleep(100))
        sched_module._tasks.append(dummy_task)

        await sched_module.stop_scheduler()
        assert sched_module._running is False
        assert len(sched_module._tasks) == 0

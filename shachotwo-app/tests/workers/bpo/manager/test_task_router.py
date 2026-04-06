"""BPO Manager — TaskRouter テスト。"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel
from workers.bpo.manager.task_router import (
    PIPELINE_REGISTRY,
    CB_FAILURE_THRESHOLD,
    CB_RECOVERY_SECONDS,
    CircuitBreakerState,
    CircuitState,
    determine_approval_required,
    route_and_execute,
    _circuit_breakers,
    _get_circuit_breaker,
    _is_circuit_open,
    _record_success,
    _record_failure,
)

COMPANY_ID = "test-company-001"


def _make_task(pipeline: str = "construction/estimation", level: int = 2, impact: float = 0.5) -> BPOTask:
    return BPOTask(
        company_id=COMPANY_ID,
        pipeline=pipeline,
        trigger_type=TriggerType.SCHEDULE,
        execution_level=ExecutionLevel(level),
        input_data={"text": "テスト"},
        estimated_impact=impact,
    )


# ─── PIPELINE_REGISTRY ───────────────────────────────────────────────────────

class TestPipelineRegistry:
    def test_construction_estimation_registered(self):
        assert "construction/estimation" in PIPELINE_REGISTRY

    def test_all_values_are_dot_paths(self):
        # internal/* キーは _pipeline 命名規則の対象外（例: run_auto_improvement_cycle）
        _INTERNAL_KEYS = {"internal/improvement_cycle", "internal/accuracy_check"}
        for key, value in PIPELINE_REGISTRY.items():
            assert "." in value, f"{key} のパスが不正: {value}"
            if key in _INTERNAL_KEYS:
                continue  # 内部システムパイプラインは命名規則を免除
            assert value.endswith(("_pipeline", "_pipeline.py")) or "pipeline" in value

    def test_registry_has_minimum_entries(self):
        assert len(PIPELINE_REGISTRY) >= 5


# ─── determine_approval_required ─────────────────────────────────────────────

class TestDetermineApprovalRequired:
    def test_level_0_never_requires_approval(self):
        task = _make_task(level=0)
        assert determine_approval_required(task) is False

    def test_level_1_never_requires_approval(self):
        task = _make_task(level=1)
        assert determine_approval_required(task) is False

    def test_level_2_low_impact_no_approval(self):
        task = _make_task(level=2, impact=0.3)
        assert determine_approval_required(task) is False

    def test_level_2_high_impact_requires_approval(self):
        task = _make_task(level=2, impact=0.9)
        assert determine_approval_required(task) is True

    def test_level_3_always_requires_approval(self):
        task = _make_task(level=3, impact=0.1)
        assert determine_approval_required(task) is True

    def test_level_4_high_trust_low_impact_no_approval(self):
        task = _make_task(level=4, impact=0.3)
        assert determine_approval_required(task, trust_score=0.96) is False

    def test_level_4_low_trust_requires_approval(self):
        task = _make_task(level=4, impact=0.3)
        assert determine_approval_required(task, trust_score=0.80) is True

    def test_level_4_high_impact_requires_approval_even_with_trust(self):
        task = _make_task(level=4, impact=0.8)
        assert determine_approval_required(task, trust_score=0.99) is True


# ─── route_and_execute ────────────────────────────────────────────────────────

class TestRouteAndExecute:
    @pytest.mark.asyncio
    async def test_unregistered_pipeline_returns_failure(self):
        task = _make_task(pipeline="nonexistent/pipeline")
        result = await route_and_execute(task)
        assert result.success is False
        assert "未登録" in result.final_output.get("error", "")

    @pytest.mark.asyncio
    async def test_approval_required_saves_pending(self):
        task = _make_task(level=3, impact=0.9)  # Level 3 → 承認必須

        with patch("workers.bpo.manager.task_router._save_approval_pending", new_callable=AsyncMock):
            result = await route_and_execute(task)

        assert result.approval_pending is True
        assert result.success is True

    @pytest.mark.asyncio
    async def test_level_0_executes_without_approval(self):
        """Level 0（通知のみ）は承認不要でパイプラインを実行しようとする"""
        task = _make_task(level=0, impact=0.5)

        mock_pipeline = AsyncMock(return_value=MagicMock(
            success=True, steps=[], final_output={},
            total_cost_yen=0.0, total_duration_ms=10, failed_step=None,
        ))

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.run_estimation_pipeline = mock_pipeline
            mock_import.return_value = mock_module

            result = await route_and_execute(task, trust_score=0.0)

        # 承認待ちではない
        assert result.approval_pending is False

    @pytest.mark.asyncio
    async def test_pipeline_import_error_returns_failure(self):
        """パイプライン未実装の場合は失敗を返す"""
        task = _make_task(level=0)  # 承認不要のレベル

        with patch("importlib.import_module", side_effect=ImportError("未実装")):
            result = await route_and_execute(task)

        assert result.success is False
        assert result.failed_step == "task_router"


# ─── Circuit Breaker ──────────────────────────────────────────────────────────

class TestCircuitBreaker:
    """Circuit Breaker のユニットテスト。各テストは _circuit_breakers を独立させるため
    テスト対象のパイプラインキーを固定し、teardown でエントリを削除する。"""

    PIPELINE = "test/cb_pipeline"

    def setup_method(self):
        """各テスト前に該当パイプラインの CB 状態をリセット。"""
        _circuit_breakers.pop(self.PIPELINE, None)

    # ── 状態遷移 ──────────────────────────────────────────────────────────────

    def test_initial_state_is_closed(self):
        cb = _get_circuit_breaker(self.PIPELINE)
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0
        assert cb.tripped_at is None

    def test_closed_circuit_is_not_open(self):
        assert _is_circuit_open(self.PIPELINE) is False

    @pytest.mark.asyncio
    async def test_trip_on_threshold_failures(self):
        """CB_FAILURE_THRESHOLD 回失敗でトリップする。"""
        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ):
            for _ in range(CB_FAILURE_THRESHOLD):
                await _record_failure(self.PIPELINE, COMPANY_ID)

        cb = _get_circuit_breaker(self.PIPELINE)
        assert cb.state == CircuitState.OPEN
        assert cb.consecutive_failures == CB_FAILURE_THRESHOLD
        assert cb.tripped_at is not None

    @pytest.mark.asyncio
    async def test_not_tripped_before_threshold(self):
        """しきい値未満では OPEN にならない。"""
        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ):
            for _ in range(CB_FAILURE_THRESHOLD - 1):
                await _record_failure(self.PIPELINE, COMPANY_ID)

        cb = _get_circuit_breaker(self.PIPELINE)
        assert cb.state == CircuitState.CLOSED
        assert _is_circuit_open(self.PIPELINE) is False

    @pytest.mark.asyncio
    async def test_open_circuit_blocks_execution(self):
        """トリップ後は _is_circuit_open が True を返す。"""
        cb = _get_circuit_breaker(self.PIPELINE)
        cb.state = CircuitState.OPEN
        cb.tripped_at = datetime.now(timezone.utc)  # 直前にトリップ

        assert _is_circuit_open(self.PIPELINE) is True

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_window(self):
        """30分経過後は HALF_OPEN に移行して実行を許可する。"""
        cb = _get_circuit_breaker(self.PIPELINE)
        cb.state = CircuitState.OPEN
        cb.tripped_at = datetime.now(timezone.utc) - timedelta(seconds=CB_RECOVERY_SECONDS + 1)

        # 30分以上経過 → ブロックしない（half-open）
        assert _is_circuit_open(self.PIPELINE) is False
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_resets_circuit_breaker(self):
        """成功で CLOSED にリセットされる。"""
        cb = _get_circuit_breaker(self.PIPELINE)
        cb.state = CircuitState.HALF_OPEN
        cb.consecutive_failures = CB_FAILURE_THRESHOLD

        await _record_success(self.PIPELINE)

        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0
        assert cb.tripped_at is None

    @pytest.mark.asyncio
    async def test_half_open_failure_retrips_immediately(self):
        """HALF_OPEN 中の失敗は即 OPEN に戻る。"""
        cb = _get_circuit_breaker(self.PIPELINE)
        cb.state = CircuitState.HALF_OPEN
        cb.consecutive_failures = CB_FAILURE_THRESHOLD  # 前回の失敗数を保持

        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ):
            await _record_failure(self.PIPELINE, COMPANY_ID)

        assert cb.state == CircuitState.OPEN
        assert cb.tripped_at is not None

    @pytest.mark.asyncio
    async def test_trip_sends_notification(self):
        """トリップ時に circuit_breaker イベントで通知が送られる。"""
        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ) as mock_notify:
            for _ in range(CB_FAILURE_THRESHOLD):
                await _record_failure(self.PIPELINE, COMPANY_ID)

        # 最後の失敗（トリップ発火）でのみ通知が呼ばれる
        calls = [c for c in mock_notify.call_args_list if c.kwargs.get("event_type") == "circuit_breaker"]
        assert len(calls) == 1
        assert calls[0].kwargs["pipeline"] == self.PIPELINE
        assert calls[0].kwargs["company_id"] == COMPANY_ID

    # ── route_and_execute との統合 ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_route_and_execute_skips_when_open(self):
        """OPEN 状態のパイプラインは route_and_execute がスキップして CB 失敗を返す。"""
        cb = _get_circuit_breaker(self.PIPELINE)
        cb.state = CircuitState.OPEN
        cb.tripped_at = datetime.now(timezone.utc)

        # PIPELINE_REGISTRY に存在しないキーで十分だが、CB チェックが先に走ることを確認するため
        # 一時的にレジストリに登録してから実行する
        task = _make_task(pipeline=self.PIPELINE, level=0)
        with patch.dict(
            "workers.bpo.manager.task_router.PIPELINE_REGISTRY",
            {self.PIPELINE: "some.module.some_func"},
        ):
            result = await route_and_execute(task)

        assert result.success is False
        assert result.failed_step == "circuit_breaker"
        assert "circuit_breaker" in (result.failed_step or "")

    @pytest.mark.asyncio
    async def test_route_and_execute_records_failure_and_trips(self):
        """パイプライン実行失敗が CB_FAILURE_THRESHOLD 回続くとトリップする。"""
        task = _make_task(pipeline="construction/estimation", level=0)
        # _circuit_breakers から対象キーを事前クリア
        _circuit_breakers.pop("construction/estimation", None)

        failing_pipeline = AsyncMock(side_effect=RuntimeError("意図的な失敗"))

        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ):
            with patch("importlib.import_module") as mock_import:
                mock_module = MagicMock()
                mock_module.run_estimation_pipeline = failing_pipeline
                mock_import.return_value = mock_module

                for _ in range(CB_FAILURE_THRESHOLD):
                    await route_and_execute(task)

        cb = _get_circuit_breaker("construction/estimation")
        assert cb.state == CircuitState.OPEN

        # クリーンアップ
        _circuit_breakers.pop("construction/estimation", None)

    @pytest.mark.asyncio
    async def test_route_and_execute_records_success_resets_cb(self):
        """パイプライン成功後に CB がリセットされる。"""
        task = _make_task(pipeline="construction/estimation", level=0)
        _circuit_breakers.pop("construction/estimation", None)

        # 失敗を数回積んでおく（しきい値未満）
        cb = _get_circuit_breaker("construction/estimation")
        cb.consecutive_failures = CB_FAILURE_THRESHOLD - 1

        mock_pipeline = AsyncMock(return_value=MagicMock(
            success=True, steps=[], final_output={},
            total_cost_yen=0.0, total_duration_ms=10, failed_step=None,
        ))

        with patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ):
            with patch("importlib.import_module") as mock_import:
                mock_module = MagicMock()
                mock_module.run_estimation_pipeline = mock_pipeline
                mock_import.return_value = mock_module

                result = await route_and_execute(task)

        assert result.success is True
        cb = _get_circuit_breaker("construction/estimation")
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

        # クリーンアップ
        _circuit_breakers.pop("construction/estimation", None)


# ─── schedule_watcher ────────────────────────────────────────────────────────

class TestScheduleWatcher:
    def test_cron_matcher_basic(self):
        from workers.bpo.manager.schedule_watcher import _matches_cron
        from datetime import datetime, timezone

        # 毎月25日9時0分
        dt = datetime(2025, 3, 25, 9, 0, tzinfo=timezone.utc)
        assert _matches_cron("0 9 25 * *", dt) is True
        assert _matches_cron("0 10 25 * *", dt) is False
        assert _matches_cron("0 9 26 * *", dt) is False

    def test_cron_wildcard(self):
        from workers.bpo.manager.schedule_watcher import _matches_cron
        from datetime import datetime, timezone

        dt = datetime(2025, 6, 15, 12, 30, tzinfo=timezone.utc)
        assert _matches_cron("* * * * *", dt) is True
        assert _matches_cron("30 12 * * *", dt) is True
        assert _matches_cron("0 12 * * *", dt) is False

    def test_builtin_triggers_all_registered_in_pipeline_registry(self):
        """BUILTIN_SCHEDULE_TRIGGERS の全 pipeline が PIPELINE_REGISTRY に登録済みであること。
        ただし internal/* プレフィックスは BPO パイプラインではなく内部システム処理のため除外する。"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS
        from workers.bpo.manager.task_router import PIPELINE_REGISTRY
        for trigger in BUILTIN_SCHEDULE_TRIGGERS:
            pipeline = trigger["pipeline"]
            if pipeline.startswith("internal/"):
                continue  # GWS Watch 更新など内部処理は PIPELINE_REGISTRY 対象外
            assert pipeline in PIPELINE_REGISTRY, (
                f"BUILTIN_SCHEDULE_TRIGGERS '{pipeline}' が PIPELINE_REGISTRY に未登録"
            )

    def test_builtin_outreach_fires_at_0800(self):
        """毎日 08:00 に outreach パイプラインが発火すること"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        dt = datetime(2025, 3, 17, 8, 0, tzinfo=timezone.utc)
        outreach = next(t for t in BUILTIN_SCHEDULE_TRIGGERS if t["pipeline"] == "sales/outreach")
        assert _matches_cron(outreach["cron_expr"], dt) is True

    def test_builtin_outreach_does_not_fire_at_0900(self):
        """09:00 に outreach は発火しないこと"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        dt = datetime(2025, 3, 17, 9, 0, tzinfo=timezone.utc)
        outreach = next(t for t in BUILTIN_SCHEDULE_TRIGGERS if t["pipeline"] == "sales/outreach")
        assert _matches_cron(outreach["cron_expr"], dt) is False

    def test_builtin_revenue_report_fires_on_day_1(self):
        """毎月1日 09:00 に revenue_report（mode=revenue）が発火すること"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        dt = datetime(2025, 4, 1, 9, 0, tzinfo=timezone.utc)
        revenue = next(
            t for t in BUILTIN_SCHEDULE_TRIGGERS
            if t["pipeline"] == "sales/revenue_report" and t["input_data"].get("mode") == "revenue"
        )
        assert _matches_cron(revenue["cron_expr"], dt) is True

    def test_builtin_revenue_report_does_not_fire_on_day_2(self):
        """毎月2日には発火しないこと"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        dt = datetime(2025, 4, 2, 9, 0, tzinfo=timezone.utc)
        revenue = next(
            t for t in BUILTIN_SCHEDULE_TRIGGERS
            if t["pipeline"] == "sales/revenue_report" and t["input_data"].get("mode") == "revenue"
        )
        assert _matches_cron(revenue["cron_expr"], dt) is False

    def test_builtin_win_loss_fires_on_monday(self):
        """毎週月曜 09:00 に win_loss_feedback が発火すること（Python weekday 0 = 月曜）"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        # 2025-03-17 は月曜
        dt = datetime(2025, 3, 17, 9, 0, tzinfo=timezone.utc)
        assert dt.weekday() == 0  # 前提確認
        win_loss = next(
            t for t in BUILTIN_SCHEDULE_TRIGGERS
            if t["pipeline"] == "sales/win_loss_feedback"
        )
        assert _matches_cron(win_loss["cron_expr"], dt) is True

    def test_builtin_win_loss_does_not_fire_on_tuesday(self):
        """火曜日には win_loss_feedback が発火しないこと"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        # 2025-03-18 は火曜
        dt = datetime(2025, 3, 18, 9, 0, tzinfo=timezone.utc)
        assert dt.weekday() == 1  # 前提確認
        win_loss = next(
            t for t in BUILTIN_SCHEDULE_TRIGGERS
            if t["pipeline"] == "sales/win_loss_feedback"
        )
        assert _matches_cron(win_loss["cron_expr"], dt) is False

    def test_builtin_cs_feedback_fires_on_last_day(self):
        """月末日 09:00 に cs_feedback が発火すること（28〜31日 + last_day_only フラグ）"""
        from workers.bpo.manager.schedule_watcher import (
            BUILTIN_SCHEDULE_TRIGGERS, _matches_cron, _is_last_day_of_month,
        )
        from datetime import datetime, timezone
        # 2025-03-31 は月末
        dt = datetime(2025, 3, 31, 9, 0, tzinfo=timezone.utc)
        cs = next(t for t in BUILTIN_SCHEDULE_TRIGGERS if t["pipeline"] == "sales/cs_feedback")
        assert _matches_cron(cs["cron_expr"], dt) is True
        assert _is_last_day_of_month(dt) is True

    def test_builtin_cs_feedback_does_not_fire_mid_month(self):
        """月中（例: 20日）には cs_feedback が発火しないこと"""
        from workers.bpo.manager.schedule_watcher import BUILTIN_SCHEDULE_TRIGGERS, _matches_cron
        from datetime import datetime, timezone
        dt = datetime(2025, 3, 20, 9, 0, tzinfo=timezone.utc)
        cs = next(t for t in BUILTIN_SCHEDULE_TRIGGERS if t["pipeline"] == "sales/cs_feedback")
        assert _matches_cron(cs["cron_expr"], dt) is False

    @pytest.mark.asyncio
    async def test_scan_schedule_triggers_builtin_fires_when_db_empty(self):
        """DB に knowledge_items がない場合も組み込みトリガーが発火すること"""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone
        from workers.bpo.manager.schedule_watcher import scan_schedule_triggers

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        # 月曜 08:00 にスキャン → outreach が発火
        # ローカルインポートのため db.supabase.get_service_client をモック
        with patch("db.supabase.get_service_client", return_value=mock_db):
            with patch(
                "workers.bpo.manager.schedule_watcher.datetime",
            ) as mock_dt:
                # 2025-03-17 月曜 08:00（outreach のみ）
                fixed_now = datetime(2025, 3, 17, 8, 0, tzinfo=timezone.utc)
                mock_dt.now.return_value = fixed_now
                tasks = await scan_schedule_triggers("test-company")

        pipeline_names = [t.pipeline for t in tasks]
        assert "sales/outreach" in pipeline_names


# ─── event_listener ───────────────────────────────────────────────────────────

class TestEventListener:
    def test_builtin_triggers_all_registered_in_pipeline_registry(self):
        """BUILTIN_EVENT_TRIGGERS の全 pipeline が PIPELINE_REGISTRY に登録済みであること"""
        from workers.bpo.manager.event_listener import BUILTIN_EVENT_TRIGGERS
        from workers.bpo.manager.task_router import PIPELINE_REGISTRY
        for trigger in BUILTIN_EVENT_TRIGGERS:
            pipeline = trigger["pipeline"]
            assert pipeline in PIPELINE_REGISTRY, (
                f"BUILTIN_EVENT_TRIGGERS '{pipeline}' が PIPELINE_REGISTRY に未登録"
            )

    def test_evaluate_condition_none_always_true(self):
        from workers.bpo.manager.event_listener import _evaluate_condition
        assert _evaluate_condition(None, {}) is True
        assert _evaluate_condition(None, {"any": "value"}) is True

    def test_evaluate_condition_gte_pass(self):
        from workers.bpo.manager.event_listener import _evaluate_condition
        cond = {"field": "lead_score", "operator": "gte", "value": 70}
        assert _evaluate_condition(cond, {"lead_score": 75}) is True
        assert _evaluate_condition(cond, {"lead_score": 70}) is True

    def test_evaluate_condition_gte_fail(self):
        from workers.bpo.manager.event_listener import _evaluate_condition
        cond = {"field": "lead_score", "operator": "gte", "value": 70}
        assert _evaluate_condition(cond, {"lead_score": 69}) is False

    def test_evaluate_condition_missing_field_is_false(self):
        from workers.bpo.manager.event_listener import _evaluate_condition
        cond = {"field": "lead_score", "operator": "gte", "value": 70}
        assert _evaluate_condition(cond, {}) is False

    def test_event_type_matches_exact(self):
        from workers.bpo.manager.event_listener import _event_type_matches
        assert _event_type_matches("lead_created", "lead_created") is True
        assert _event_type_matches("lead_created", "lead_updated") is False

    def test_event_type_matches_wildcard(self):
        from workers.bpo.manager.event_listener import _event_type_matches
        assert _event_type_matches("freee.expense.*", "freee.expense.created") is True
        assert _event_type_matches("freee.expense.*", "freee.employee.created") is False

    @pytest.mark.asyncio
    async def test_handle_webhook_lead_created_builtin(self):
        """lead_created イベントで lead_qualification パイプラインが返ること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="lead_created",
                payload={"lead_id": "L001", "company_name": "テスト株式会社"},
            )

        assert task is not None
        assert task.pipeline == "sales/lead_qualification"
        assert task.input_data.get("lead_id") == "L001"

    @pytest.mark.asyncio
    async def test_handle_webhook_lead_score_gte_70_condition_met(self):
        """lead_score_gte_70 + payload.lead_score=80 で提案書生成パイプラインが返ること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="lead_score_gte_70",
                payload={"lead_id": "L002", "lead_score": 80},
            )

        assert task is not None
        assert task.pipeline == "sales/proposal_generation"

    @pytest.mark.asyncio
    async def test_handle_webhook_lead_score_condition_not_met(self):
        """lead_score=65 の場合は条件不成立でNoneが返ること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="lead_score_gte_70",
                payload={"lead_id": "L003", "lead_score": 65},
            )

        assert task is None

    @pytest.mark.asyncio
    async def test_handle_webhook_cancellation_approval_gated(self):
        """cancellation_requested は APPROVAL_GATED (level=3) で返ること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.models import ExecutionLevel
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="cancellation_requested",
                payload={"customer_id": "C100", "reason": "コスト削減"},
            )

        assert task is not None
        assert task.pipeline == "sales/cancellation"
        assert task.execution_level == ExecutionLevel.APPROVAL_GATED
        assert task.estimated_impact == 0.9

    @pytest.mark.asyncio
    async def test_handle_webhook_health_score_high_condition_met(self):
        """health_score=85 の場合アップセル提案パイプラインが返ること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="health_score_high",
                payload={"customer_id": "C200", "health_score": 85, "unused_modules": ["payroll"]},
            )

        assert task is not None
        assert task.pipeline == "sales/upsell_briefing"

    @pytest.mark.asyncio
    async def test_handle_webhook_db_override_skips_builtin(self):
        """DB に lead_created が登録済みの場合、組み込みをスキップすること"""
        from unittest.mock import MagicMock, patch
        from workers.bpo.manager.event_listener import handle_webhook

        mock_db = MagicMock()
        # DB に lead_created → custom/pipeline が登録されているケース
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": "ki-001",
                "title": "カスタムリード処理",
                "confidence": 0.9,
                "metadata": {
                    "trigger_type": "event",
                    "event_type": "lead_created",
                    "pipeline": "custom/lead_pipeline",
                    "input_data": {"source": "custom"},
                    "execution_level": 2,
                },
            }
        ]

        with patch("db.supabase.get_service_client", return_value=mock_db):
            task = await handle_webhook(
                company_id="test-co",
                event_type="lead_created",
                payload={"lead_id": "L999"},
            )

        assert task is not None
        # DB 登録のカスタムパイプラインが選ばれること
        assert task.pipeline == "custom/lead_pipeline"


# ─── condition_evaluator / builtin_sales_chains ──────────────────────────────

class TestBuiltinSalesConditionChains:
    def test_builtin_chains_all_pipelines_in_registry(self):
        """BUILTIN_SALES_CONDITION_CHAINS の全 target_pipeline が PIPELINE_REGISTRY に登録済みであること"""
        from workers.bpo.manager.condition_evaluator import BUILTIN_SALES_CONDITION_CHAINS
        from workers.bpo.manager.task_router import PIPELINE_REGISTRY
        for chain in BUILTIN_SALES_CONDITION_CHAINS:
            pipeline = chain["target_pipeline"]
            assert pipeline in PIPELINE_REGISTRY, (
                f"BUILTIN_SALES_CONDITION_CHAINS '{pipeline}' が PIPELINE_REGISTRY に未登録"
            )

    def test_all_chains_have_required_fields(self):
        """各チェーンに必須フィールドが揃っていること"""
        from workers.bpo.manager.condition_evaluator import BUILTIN_SALES_CONDITION_CHAINS
        required = {"name", "source_condition", "target_pipeline", "execution_level", "estimated_impact"}
        for chain in BUILTIN_SALES_CONDITION_CHAINS:
            missing = required - set(chain.keys())
            assert not missing, f"chain '{chain.get('name', '?')}' に不足フィールド: {missing}"

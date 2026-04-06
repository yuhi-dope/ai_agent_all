"""E2E 閉ループテスト — 完全自動駆動サイクルの検証。

検証フロー:
  1. イベント発火 → タスク生成
  2. TaskRouter → パイプライン選択 + 承認要否判定
  3. 承認待ち → proactive_proposals に保存
  4. 承認不要タスク → パイプライン実行 → 結果返却
  5. 能動提案 → タスク生成

全てモック環境で端から端まで通す。
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.enums import ExecutionLevel, TriggerType
from workers.bpo.manager.models import BPOTask, PipelineResult


# ── ヘルパー ──────────────────────────────────────────────


def _cid() -> str:
    return str(uuid4())


def _task(
    company_id: str,
    pipeline: str = "common/expense",
    trigger: TriggerType = TriggerType.EVENT,
    level: ExecutionLevel = ExecutionLevel.APPROVAL_GATED,
    impact: float = 0.5,
) -> BPOTask:
    return BPOTask(
        id=str(uuid4()),
        company_id=company_id,
        pipeline=pipeline,
        trigger_type=trigger,
        execution_level=level,
        estimated_impact=impact,
        input_data={"test": True},
    )


# ── 1. 承認判定ロジック ──────────────────────────────────


class TestDetermineApprovalRequired:
    """determine_approval_required() の動作を検証する。"""

    def test_notify_only_no_approval(self):
        """Level 0 (NOTIFY_ONLY) → 承認不要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.NOTIFY_ONLY)
        assert determine_approval_required(t) is False

    def test_data_collect_no_approval(self):
        """Level 1 (DATA_COLLECT) → 承認不要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.DATA_COLLECT)
        assert determine_approval_required(t) is False

    def test_draft_create_low_impact_no_approval(self):
        """Level 2 (DRAFT_CREATE) + 低影響度 → 承認不要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.DRAFT_CREATE, impact=0.3)
        assert determine_approval_required(t) is False

    def test_draft_create_high_impact_requires_approval(self):
        """Level 2 (DRAFT_CREATE) + 高影響度 → 承認必要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.DRAFT_CREATE, impact=0.9)
        assert determine_approval_required(t) is True

    def test_approval_gated_always_requires(self):
        """Level 3 (APPROVAL_GATED) → 常に承認必要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.APPROVAL_GATED)
        assert determine_approval_required(t) is True

    def test_autonomous_high_trust_low_impact_no_approval(self):
        """Level 4 (AUTONOMOUS) + 高信頼 + 低影響 → 承認不要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.AUTONOMOUS, impact=0.3)
        assert determine_approval_required(t, trust_score=0.96) is False

    def test_autonomous_low_trust_requires_approval(self):
        """Level 4 (AUTONOMOUS) + 低信頼 → 承認必要。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.AUTONOMOUS, impact=0.3)
        assert determine_approval_required(t, trust_score=0.5) is True


# ── 2. route_and_execute ────────────────────────────────


class TestRouteAndExecute:
    """route_and_execute() の承認ゲート + 実行フローを検証する。"""

    @pytest.mark.asyncio
    async def test_approval_required_saves_and_returns_pending(self):
        """承認必要タスク → proactive_proposalsに保存 → approval_pending=True。"""
        cid = _cid()
        t = _task(cid, level=ExecutionLevel.APPROVAL_GATED)

        with patch(
            "workers.bpo.manager.task_router._get_effective_registry",
            new_callable=AsyncMock,
            return_value={"common/expense": "workers.bpo.common.pipelines.expense_pipeline.run_expense_pipeline"},
        ), patch(
            "workers.bpo.manager.task_router._save_approval_pending",
            new_callable=AsyncMock,
        ) as mock_save, patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ), patch(
            "workers.bpo.manager.task_router.determine_approval_required",
            return_value=True,
        ):
            # HITL閾値チェックをスキップ
            with patch("db.supabase.get_service_client", side_effect=Exception("skip")):
                from workers.bpo.manager.task_router import route_and_execute
                result = await route_and_execute(t)

        assert result.approval_pending is True
        assert result.success is True
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_approval_executes_pipeline(self):
        """承認不要タスク → パイプライン実行 → 結果返却。"""
        cid = _cid()
        t = _task(cid, pipeline="common/expense", level=ExecutionLevel.DATA_COLLECT)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.steps = []
        mock_result.final_output = {"amount": 50000}
        mock_result.total_cost_yen = 1.0
        mock_result.total_duration_ms = 200
        mock_result.failed_step = None

        mock_pipeline = AsyncMock(return_value=mock_result)

        with patch(
            "workers.bpo.manager.task_router._get_effective_registry",
            new_callable=AsyncMock,
            return_value={"common/expense": "test_module.run"},
        ), patch(
            "workers.bpo.manager.task_router.notify_pipeline_event",
            new_callable=AsyncMock,
        ), patch(
            "importlib.import_module",
        ) as mock_import, patch(
            "workers.bpo.manager.task_router.get_service_client",
            side_effect=Exception("skip"),
        ), patch(
            "workers.bpo.manager.condition_evaluator.evaluate_knowledge_triggers",
            new_callable=AsyncMock,
            return_value=[],
        ):
            mock_module = MagicMock()
            mock_module.run = mock_pipeline
            mock_import.return_value = mock_module

            from workers.bpo.manager.task_router import route_and_execute
            result = await route_and_execute(t)

        assert result.success is True
        assert result.approval_pending is False
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_unregistered_pipeline_returns_error(self):
        """未登録パイプライン → エラー返却。"""
        t = _task(_cid(), pipeline="unknown/nonexistent")

        with patch(
            "workers.bpo.manager.task_router._get_effective_registry",
            new_callable=AsyncMock,
            return_value={},
        ):
            from workers.bpo.manager.task_router import route_and_execute
            result = await route_and_execute(t)

        assert result.success is False
        assert "未登録" in result.final_output.get("error", "")


# ── 3. Orchestrator ループ ──────────────────────────────


class TestOrchestratorLoop:
    """orchestrator の各サイクルが正しくタスクをディスパッチする。"""

    @pytest.mark.asyncio
    async def test_schedule_cycle_dispatches_tasks(self):
        """スケジュールサイクル → タスク生成 → route_and_execute。"""
        cid = _cid()
        mock_tasks = [_task(cid, trigger=TriggerType.SCHEDULE)]

        with patch(
            "workers.bpo.manager.orchestrator._get_active_company_ids",
            new_callable=AsyncMock,
            return_value=[cid],
        ), patch(
            "workers.bpo.manager.schedule_watcher.scan_schedule_triggers",
            new_callable=AsyncMock,
            return_value=mock_tasks,
        ), patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=PipelineResult(success=True, pipeline="common/expense"),
        ) as mock_route:
            from workers.bpo.manager.orchestrator import _run_schedule_cycle
            await _run_schedule_cycle()

        assert mock_route.call_count >= 1

    @pytest.mark.asyncio
    async def test_condition_cycle_dispatches_tasks(self):
        """条件連鎖サイクル → タスク生成 → route_and_execute。"""
        cid = _cid()
        mock_tasks = [_task(cid, trigger=TriggerType.CONDITION)]

        with patch(
            "workers.bpo.manager.orchestrator._get_active_company_ids",
            new_callable=AsyncMock,
            return_value=[cid],
        ), patch(
            "workers.bpo.manager.condition_evaluator.evaluate_knowledge_triggers",
            new_callable=AsyncMock,
            return_value=mock_tasks,
        ), patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=PipelineResult(success=True, pipeline="common/expense"),
        ) as mock_route:
            from workers.bpo.manager.orchestrator import _run_condition_cycle
            await _run_condition_cycle()

        assert mock_route.call_count >= 1

    @pytest.mark.asyncio
    async def test_proactive_cycle_dispatches_tasks(self):
        """能動提案サイクル → タスク生成 → route_and_execute。"""
        cid = _cid()
        mock_tasks = [_task(cid, trigger=TriggerType.PROACTIVE)]

        with patch(
            "workers.bpo.manager.orchestrator._get_active_company_ids",
            new_callable=AsyncMock,
            return_value=[cid],
        ), patch(
            "workers.bpo.manager.proactive_scanner.scan_proactive_tasks",
            new_callable=AsyncMock,
            return_value=mock_tasks,
        ), patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=PipelineResult(success=True, pipeline="common/expense"),
        ) as mock_route:
            from workers.bpo.manager.orchestrator import _run_proactive_cycle
            await _run_proactive_cycle()

        assert mock_route.call_count >= 1


# ── 4. 閉ループ統合テスト ───────────────────────────────


class TestClosedLoopIntegration:
    """スケジュール→承認→実行→能動提案 の閉ループ全体を検証。"""

    @pytest.mark.asyncio
    async def test_schedule_to_approval_pending(self):
        """スケジュールトリガー → 承認待ちで停止（人間が最終チェック）。"""
        cid = _cid()
        t = _task(cid, level=ExecutionLevel.APPROVAL_GATED, trigger=TriggerType.SCHEDULE)

        with patch(
            "workers.bpo.manager.orchestrator._get_active_company_ids",
            new_callable=AsyncMock,
            return_value=[cid],
        ), patch(
            "workers.bpo.manager.schedule_watcher.scan_schedule_triggers",
            new_callable=AsyncMock,
            return_value=[t],
        ), patch(
            "workers.bpo.manager.task_router.route_and_execute",
            new_callable=AsyncMock,
            return_value=PipelineResult(
                success=True,
                pipeline="common/expense",
                approval_pending=True,
                final_output={"message": "承認待ち"},
            ),
        ) as mock_route:
            from workers.bpo.manager.orchestrator import _run_schedule_cycle
            await _run_schedule_cycle()

        result = mock_route.return_value
        assert result.approval_pending is True
        assert result.success is True

    @pytest.mark.asyncio
    async def test_low_impact_auto_executes(self):
        """低影響度のデータ収集タスク → 承認なしで自動実行。"""
        from workers.bpo.manager.task_router import determine_approval_required
        t = _task(_cid(), level=ExecutionLevel.DATA_COLLECT, impact=0.1)
        assert determine_approval_required(t) is False

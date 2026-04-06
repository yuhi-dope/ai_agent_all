"""orchestrator kill switch のユニットテスト。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────
# _is_global_kill_switch_on
# ─────────────────────────────────────────────────────────────────

class TestIsGlobalKillSwitchOn:
    def test_env_true_lowercase(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "true"}):
            assert _is_global_kill_switch_on() is True

    def test_env_true_uppercase(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "TRUE"}):
            assert _is_global_kill_switch_on() is True

    def test_env_one(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "1"}):
            assert _is_global_kill_switch_on() is True

    def test_env_yes(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "yes"}):
            assert _is_global_kill_switch_on() is True

    def test_env_false(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "false"}):
            assert _is_global_kill_switch_on() is False

    def test_env_not_set(self):
        from workers.bpo.manager.orchestrator import _is_global_kill_switch_on
        import os
        env = {k: v for k, v in os.environ.items() if k != "BPO_KILL_SWITCH"}
        with patch.dict("os.environ", env, clear=True):
            assert _is_global_kill_switch_on() is False


# ─────────────────────────────────────────────────────────────────
# _is_tenant_kill_switch_on
# ─────────────────────────────────────────────────────────────────

class TestIsTenantKillSwitchOn:
    @pytest.mark.asyncio
    async def test_returns_true_when_flag_set(self):
        from workers.bpo.manager.orchestrator import _is_tenant_kill_switch_on

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "bpo_kill_switch": True
        }

        with patch("workers.bpo.manager.orchestrator.get_service_client", return_value=mock_db, create=True):
            with patch("db.supabase.get_service_client", return_value=mock_db):
                result = await _is_tenant_kill_switch_on("company-abc-123")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_flag_not_set(self):
        from workers.bpo.manager.orchestrator import _is_tenant_kill_switch_on

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "bpo_kill_switch": False
        }

        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _is_tenant_kill_switch_on("company-abc-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_row_missing(self):
        from workers.bpo.manager.orchestrator import _is_tenant_kill_switch_on

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None

        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _is_tenant_kill_switch_on("company-abc-123")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_db_error(self):
        """DB例外時はFalse（停止しない）として安全側に倒す。"""
        from workers.bpo.manager.orchestrator import _is_tenant_kill_switch_on

        mock_db = MagicMock()
        mock_db.table.side_effect = Exception("DB接続エラー")

        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _is_tenant_kill_switch_on("company-abc-123")

        assert result is False


# ─────────────────────────────────────────────────────────────────
# _get_active_company_ids — bpo_kill_switch 除外ロジック
# ─────────────────────────────────────────────────────────────────

class TestGetActiveCompanyIds:
    @pytest.mark.asyncio
    async def test_excludes_kill_switch_tenants(self):
        """bpo_kill_switch=True のテナントは返さない。"""
        from workers.bpo.manager.orchestrator import _get_active_company_ids

        mock_db = MagicMock()
        # neq("bpo_kill_switch", True) フィルタが成功するケース
        mock_result = MagicMock()
        mock_result.data = [{"id": "company-active-001"}, {"id": "company-active-002"}]
        (
            mock_db.table.return_value
            .select.return_value
            .eq.return_value
            .neq.return_value
            .execute.return_value
        ) = mock_result

        with patch("db.supabase.get_service_client", return_value=mock_db):
            ids = await _get_active_company_ids()

        assert ids == ["company-active-001", "company-active-002"]

    @pytest.mark.asyncio
    async def test_fallback_when_column_missing(self):
        """bpo_kill_switch カラムが存在しない場合は is_active のみでフィルタ。"""
        from workers.bpo.manager.orchestrator import _get_active_company_ids

        mock_db = MagicMock()

        # neq() 呼び出し時に例外 → フォールバックが動く
        neq_chain = MagicMock()
        neq_chain.execute.side_effect = Exception("column does not exist")
        mock_db.table.return_value.select.return_value.eq.return_value.neq.return_value = neq_chain

        # フォールバック: is_active のみ
        fallback_result = MagicMock()
        fallback_result.data = [{"id": "company-fallback-001"}]
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = fallback_result

        with patch("db.supabase.get_service_client", return_value=mock_db):
            ids = await _get_active_company_ids()

        assert ids == ["company-fallback-001"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        """DB全体エラー時は空リストを返す。"""
        from workers.bpo.manager.orchestrator import _get_active_company_ids

        mock_db = MagicMock()
        mock_db.table.side_effect = Exception("接続失敗")

        with patch("db.supabase.get_service_client", return_value=mock_db):
            ids = await _get_active_company_ids()

        assert ids == []


# ─────────────────────────────────────────────────────────────────
# _orchestrator_loop — グローバルkill switchでスキップ
# ─────────────────────────────────────────────────────────────────

class TestOrchestratorLoopKillSwitch:
    @pytest.mark.asyncio
    async def test_global_kill_switch_skips_cycles(self):
        """グローバルkill switch ON 時はサイクルを実行しない。"""
        from workers.bpo.manager import orchestrator as orch_module

        schedule_called = False
        notify_called = False

        async def fake_schedule():
            nonlocal schedule_called
            schedule_called = True

        async def fake_notify(**kwargs):
            nonlocal notify_called
            notify_called = True
            return True

        async def fake_sleep(n):
            # 1回だけループを回してキャンセル
            raise asyncio.CancelledError()

        with patch.dict("os.environ", {"BPO_KILL_SWITCH": "true"}):
            with patch.object(orch_module, "_run_schedule_cycle", fake_schedule):
                with patch(
                    "workers.bpo.manager.notifier.notify_pipeline_event",
                    new=fake_notify,
                ):
                    with patch("asyncio.sleep", fake_sleep):
                        with pytest.raises(asyncio.CancelledError):
                            await orch_module._orchestrator_loop()

        # グローバルkill switch ON → スケジュールサイクルは呼ばれない
        assert schedule_called is False

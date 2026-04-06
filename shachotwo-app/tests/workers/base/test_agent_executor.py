"""
AgentExecutor の基本動作テスト。

外部依存（LLM / Supabase / LangGraph）は全てモック。
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.base.agent_executor import (
    AgentExecutor,
    AgentState,
    _HitLInterrupt,
    build_simple_graph,
    require_human_approval,
)


# ─── テスト用ヘルパー ─────────────────────────────────────────────────────────

def _make_state(**overrides: Any) -> AgentState:
    """テスト用初期ステートを生成する。"""
    base: AgentState = {
        "task_id": "test-task-001",
        "company_id": "company-abc",
        "pipeline_name": "construction/estimation",
        "context": {},
        "steps_completed": [],
        "human_approval_pending": False,
        "approval_execution_id": None,
        "current_step": "",
        "final_output": None,
        "error": None,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_db_client(
    hitl_row: dict | None = None,
    insert_returns: Any = None,
) -> MagicMock:
    """
    Supabase クライアントのモックを生成する。
    チェーンメソッド（.table().select().eq()...execute()）を AsyncMock で再現。
    """
    client = MagicMock()

    # デフォルトの execute() レスポンス
    default_resp = MagicMock()
    default_resp.data = hitl_row

    execute_mock = AsyncMock(return_value=default_resp)

    # チェーン先を全て同一オブジェクトに向ける
    chain = MagicMock()
    chain.execute = execute_mock
    chain.select = MagicMock(return_value=chain)
    chain.eq = MagicMock(return_value=chain)
    chain.single = MagicMock(return_value=chain)
    chain.insert = MagicMock(return_value=chain)
    chain.update = MagicMock(return_value=chain)

    client.table = MagicMock(return_value=chain)
    return client


# ─── AgentExecutor.run ────────────────────────────────────────────────────────

class TestAgentExecutorRun:
    """AgentExecutor.run() の基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_run_graph_is_none_returns_state_unchanged(self):
        """graph=None の場合、ステートをそのまま返す。"""
        executor = AgentExecutor(graph=None, db_client=None)
        state = _make_state()
        result = await executor.run(state)

        assert result["task_id"] == "test-task-001"
        assert result["human_approval_pending"] is False
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_run_successful_graph_returns_final_output(self):
        """グラフが正常終了した場合、final_output が格納される。"""
        expected_output = {"total_cost": 1_000_000, "items": []}

        graph_mock = MagicMock()
        graph_mock.ainvoke = AsyncMock(
            return_value=_make_state(final_output=expected_output)
        )

        executor = AgentExecutor(graph=graph_mock, db_client=None)
        state = _make_state()
        result = await executor.run(state)

        assert result["final_output"] == expected_output
        assert result["human_approval_pending"] is False
        graph_mock.ainvoke.assert_awaited_once_with(state)

    @pytest.mark.asyncio
    async def test_run_graph_raises_unexpected_error_sets_error_field(self):
        """グラフが予期しない例外を raise した場合、error フィールドに格納する。"""
        graph_mock = MagicMock()
        graph_mock.ainvoke = AsyncMock(side_effect=RuntimeError("DB 接続エラー"))

        executor = AgentExecutor(graph=graph_mock, db_client=None)
        state = _make_state()
        result = await executor.run(state)

        assert result["error"] == "DB 接続エラー"
        assert result["human_approval_pending"] is False

    @pytest.mark.asyncio
    async def test_run_hitl_interrupt_sets_pending_flag(self):
        """_HitLInterrupt が raise された場合、human_approval_pending=True になる。"""
        interrupted_state = _make_state(
            final_output={"total_cost": 500_000},
            current_step="hitl_check",
        )

        async def _raise_hitl(s: AgentState) -> AgentState:
            raise _HitLInterrupt(interrupted_state)

        graph_mock = MagicMock()
        graph_mock.ainvoke = _raise_hitl

        executor = AgentExecutor(graph=graph_mock, db_client=None)
        state = _make_state()
        result = await executor.run(state)

        assert result["human_approval_pending"] is True
        assert result["approval_execution_id"] is not None

    @pytest.mark.asyncio
    async def test_run_hitl_interrupt_persists_to_db(self):
        """HitL 中断時に execution_logs への INSERT が呼ばれる。"""
        db_client = _make_db_client()
        interrupted_state = _make_state(
            final_output={"total_cost": 800_000},
            current_step="hitl_check",
        )

        async def _raise_hitl(s: AgentState) -> AgentState:
            raise _HitLInterrupt(interrupted_state)

        graph_mock = MagicMock()
        graph_mock.ainvoke = _raise_hitl

        executor = AgentExecutor(graph=graph_mock, db_client=db_client)
        result = await executor.run(_make_state())

        assert result["human_approval_pending"] is True
        # table("execution_logs") が呼ばれていること
        db_client.table.assert_called_with("execution_logs")

    @pytest.mark.asyncio
    async def test_run_node_sets_pending_flag_directly(self):
        """
        ノードが _HitLInterrupt を raise しなくても human_approval_pending=True を
        セットした場合、execution_id が付与される。
        """
        pending_state = _make_state(
            human_approval_pending=True,
            approval_execution_id=None,  # まだ ID が付与されていない
        )
        graph_mock = MagicMock()
        graph_mock.ainvoke = AsyncMock(return_value=pending_state)

        executor = AgentExecutor(graph=graph_mock, db_client=None)
        result = await executor.run(_make_state())

        assert result["human_approval_pending"] is True
        assert result["approval_execution_id"] is not None


# ─── AgentExecutor.resume ─────────────────────────────────────────────────────

class TestAgentExecutorResume:
    """AgentExecutor.resume() の基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_resume_db_client_none_raises_value_error(self):
        """db_client=None の場合は ValueError を raise する。"""
        executor = AgentExecutor(graph=None, db_client=None)
        with pytest.raises(ValueError, match="execution_id"):
            await executor.resume("non-existent-id", {})

    @pytest.mark.asyncio
    async def test_resume_merges_approved_output_into_context(self):
        """
        承認後の resume で approved_output が context にマージされる。
        """
        saved_state = _make_state(
            task_id="task-xyz",
            company_id="company-abc",
            context={"original_key": "original_value"},
            steps_completed=["extract_quantities"],
            current_step="hitl_check",
        )

        # DB の _load_pending をモック
        executor = AgentExecutor(graph=None, db_client=MagicMock())
        executor._load_pending = AsyncMock(return_value=saved_state)
        executor._mark_approved = AsyncMock()

        approved = {"total_cost": 1_200_000, "items": [{"category": "土工"}]}
        result = await executor.resume("exec-id-001", approved)

        # context に approved_output がマージされていること
        assert result["context"]["approved_output"] == approved
        assert result["human_approval_pending"] is False
        executor._mark_approved.assert_awaited_once_with("exec-id-001", approved)

    @pytest.mark.asyncio
    async def test_resume_calls_run_after_loading_state(self):
        """resume は load 後に run を呼び出す。"""
        saved_state = _make_state(context={"input_data": {}})

        executor = AgentExecutor(graph=None, db_client=MagicMock())
        executor._load_pending = AsyncMock(return_value=saved_state)
        executor._mark_approved = AsyncMock()

        result = await executor.resume("exec-id-002", {"ok": True})
        # graph=None なので run はそのまま state を返す
        assert result["task_id"] == saved_state["task_id"]


# ─── _persist_pending ─────────────────────────────────────────────────────────

class TestPersistPending:
    """_persist_pending の単体テスト。"""

    @pytest.mark.asyncio
    async def test_persist_pending_returns_uuid_without_db(self):
        """db_client=None でも UUID を返す。"""
        executor = AgentExecutor(db_client=None)
        state = _make_state(final_output={"total_cost": 0})
        exec_id = await executor._persist_pending(state)
        # UUID 形式であること
        uuid.UUID(exec_id)  # 例外が出なければ OK

    @pytest.mark.asyncio
    async def test_persist_pending_inserts_pending_status(self):
        """DB クライアントがある場合、approval_status='pending' で INSERT される。"""
        db_client = _make_db_client()
        executor = AgentExecutor(db_client=db_client)
        state = _make_state(
            company_id="company-xyz",
            pipeline_name="construction/estimation",
            final_output={"total_cost": 300_000},
        )
        exec_id = await executor._persist_pending(state)

        assert exec_id  # UUID 文字列
        # INSERT が呼ばれていること
        db_client.table.assert_called_with("execution_logs")


# ─── require_human_approval ───────────────────────────────────────────────────

class TestRequireHumanApproval:
    """require_human_approval ヘルパーのテスト。"""

    def test_raises_hitl_interrupt(self):
        """常に _HitLInterrupt を raise する。"""
        state = _make_state()
        with pytest.raises(_HitLInterrupt) as exc_info:
            require_human_approval(state)
        assert exc_info.value.state["task_id"] == "test-task-001"


# ─── build_simple_graph ───────────────────────────────────────────────────────

class TestBuildSimpleGraph:
    """build_simple_graph のテスト（LangGraph 未インストール時の挙動含む）。"""

    def test_returns_none_when_langgraph_unavailable(self):
        """LangGraph が未インストールの場合、None を返す。"""
        import workers.base.agent_executor as mod

        original = mod._LANGGRAPH_AVAILABLE
        try:
            mod._LANGGRAPH_AVAILABLE = False
            result = build_simple_graph(
                nodes=[("node_a", lambda s: s)],
                entry_point="node_a",
            )
            assert result is None
        finally:
            mod._LANGGRAPH_AVAILABLE = original

    def test_returns_compiled_graph_when_langgraph_available(self):
        """LangGraph がインストール済みの場合、CompiledGraph を返す。"""
        try:
            import langgraph  # noqa: F401
        except ImportError:
            pytest.skip("langgraph 未インストール")

        async def node_a(state: AgentState) -> AgentState:
            return state

        graph = build_simple_graph(
            nodes=[("node_a", node_a)],
            entry_point="node_a",
        )
        assert graph is not None
        assert hasattr(graph, "ainvoke")

"""BPO LangGraph: SaaS BPO パイプラインのグラフ定義。

Phase 1: task_planner → END（awaiting_approval で停止）
Phase 2: saas_executor → result_reporter → END（承認後に実行）
"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END

from agent.state import BPOState
from agent.config import STEP_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS
from bpo_agent.nodes.task_planner import task_planner_node
from bpo_agent.nodes.saas_executor import bpo_saas_executor_node
from bpo_agent.nodes.result_reporter import result_reporter_node


def _wrap_node_with_timeout(
    node_fn: Callable[[BPOState], dict],
    timeout_seconds: int = STEP_TIMEOUT_SECONDS,
):
    """ノード実行を Step 毎のタイムアウトでラップする。"""

    def wrapped(state: BPOState, config: Optional[RunnableConfig] = None) -> dict:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(node_fn, state)
            try:
                return future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                error_logs = list(state.get("error_logs") or [])
                error_logs.append(f"BPO step timeout ({timeout_seconds}s)")
                return {"error_logs": error_logs, "status": "failed"}

    return wrapped


def build_bpo_plan_graph():
    """Phase 1: タスク計画生成 → END（awaiting_approval で停止）。"""
    graph = StateGraph(BPOState)
    graph.add_node("task_planner", _wrap_node_with_timeout(task_planner_node))
    graph.set_entry_point("task_planner")
    graph.add_edge("task_planner", END)
    return graph.compile()


def build_bpo_exec_graph():
    """Phase 2: 承認後の SaaS 操作実行 + 結果レポート。"""
    graph = StateGraph(BPOState)
    graph.add_node("saas_executor", _wrap_node_with_timeout(bpo_saas_executor_node))
    graph.add_node("result_reporter", _wrap_node_with_timeout(result_reporter_node))
    graph.set_entry_point("saas_executor")
    graph.add_edge("saas_executor", "result_reporter")
    graph.add_edge("result_reporter", END)
    return graph.compile()


# シングルトンキャッシュ
_bpo_plan_app = None
_bpo_exec_app = None


def get_bpo_plan_graph():
    """Phase 1 コンパイル済みグラフを返す。"""
    global _bpo_plan_app
    if _bpo_plan_app is None:
        _bpo_plan_app = build_bpo_plan_graph()
    return _bpo_plan_app


def get_bpo_exec_graph():
    """Phase 2 コンパイル済みグラフを返す。"""
    global _bpo_exec_app
    if _bpo_exec_app is None:
        _bpo_exec_app = build_bpo_exec_graph()
    return _bpo_exec_app


def invoke_bpo_plan(state: BPOState, *, config: Optional[dict] = None):
    """Phase 1 実行: タスク計画生成。"""
    app = get_bpo_plan_graph()
    config = config or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.invoke, state, config)
        try:
            return future.result(timeout=TOTAL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            error_logs = list(state.get("error_logs") or [])
            error_logs.append(f"BPO plan timeout ({TOTAL_TIMEOUT_SECONDS}s)")
            return {**state, "error_logs": error_logs, "status": "timeout"}


def invoke_bpo_exec(state: BPOState, *, config: Optional[dict] = None):
    """Phase 2 実行: SaaS 操作 + 結果レポート。"""
    app = get_bpo_exec_graph()
    config = config or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.invoke, state, config)
        try:
            return future.result(timeout=TOTAL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            error_logs = list(state.get("error_logs") or [])
            error_logs.append(f"BPO exec timeout ({TOTAL_TIMEOUT_SECONDS}s)")
            return {**state, "error_logs": error_logs, "status": "timeout"}

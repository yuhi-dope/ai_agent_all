"""BPO LangGraph: SaaS BPO パイプラインのグラフ定義。

Phase 1: task_planner → END（awaiting_approval で停止）
Phase 2: saas_executor → result_reporter → END（承認後に実行）

タイムアウトは各 LLM クライアントの request_timeout に委譲する。
エラーが出ない限り処理を継続する。
"""
from __future__ import annotations

from typing import Optional
from langgraph.graph import StateGraph, END

from agent.state import BPOState
from bpo_agent.nodes.task_planner import task_planner_node
from bpo_agent.nodes.saas_executor import bpo_saas_executor_node
from bpo_agent.nodes.result_reporter import result_reporter_node


def build_bpo_plan_graph():
    """Phase 1: タスク計画生成 → END（awaiting_approval で停止）。"""
    graph = StateGraph(BPOState)
    graph.add_node("task_planner", task_planner_node)
    graph.set_entry_point("task_planner")
    graph.add_edge("task_planner", END)
    return graph.compile()


def build_bpo_exec_graph():
    """Phase 2: 承認後の SaaS 操作実行 + 結果レポート。"""
    graph = StateGraph(BPOState)
    graph.add_node("saas_executor", bpo_saas_executor_node)
    graph.add_node("result_reporter", result_reporter_node)
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
    """Phase 1 実行: タスク計画生成。エラーが出ない限り完了まで待つ。"""
    app = get_bpo_plan_graph()
    return app.invoke(state, config or {})


def invoke_bpo_exec(state: BPOState, *, config: Optional[dict] = None):
    """Phase 2 実行: SaaS 操作 + 結果レポート。エラーが出ない限り完了まで待つ。"""
    app = get_bpo_exec_graph()
    return app.invoke(state, config or {})

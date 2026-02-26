"""LangGraph メインロジック: コード生成パイプラインのグラフ定義。"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.config import STEP_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS
from develop_agent.nodes.genre_classifier import genre_classifier_node
from develop_agent.nodes.spec_agent import spec_agent_node
from develop_agent.nodes.coder_agent import coder_agent_node
from develop_agent.nodes.review_guardrails import review_guardrails_node, route_after_review
from develop_agent.nodes.fix_agent import fix_agent_node
from develop_agent.nodes.github_publisher import github_publisher_node


def _wrap_node_with_timeout(
    node_fn: Callable[[AgentState], dict],
    timeout_seconds: int = STEP_TIMEOUT_SECONDS,
):
    """ノード実行を Step 毎のタイムアウトでラップする。"""

    def wrapped(state: AgentState, config: Optional[RunnableConfig] = None) -> dict:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(node_fn, state)
            try:
                return future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                error_logs = list(state.get("error_logs") or [])
                error_logs.append(f"Step timeout ({timeout_seconds}s)")
                return {"error_logs": error_logs, "status": "review_ng"}

    return wrapped


def build_graph():
    """StateGraph を組み立ててコンパイルする。"""
    graph = StateGraph(AgentState)

    graph.add_node("genre_classifier", _wrap_node_with_timeout(genre_classifier_node))
    graph.add_node("spec_agent", _wrap_node_with_timeout(spec_agent_node))
    graph.add_node("coder_agent", _wrap_node_with_timeout(coder_agent_node))
    graph.add_node("review_guardrails", _wrap_node_with_timeout(review_guardrails_node))
    graph.add_node("fix_agent", _wrap_node_with_timeout(fix_agent_node))
    graph.add_node("github_publisher", _wrap_node_with_timeout(github_publisher_node))

    graph.set_entry_point("genre_classifier")
    graph.add_edge("genre_classifier", "spec_agent")
    graph.add_edge("spec_agent", "coder_agent")
    graph.add_edge("coder_agent", "review_guardrails")
    graph.add_conditional_edges(
        "review_guardrails",
        route_after_review,
        {
            "github_publisher": "github_publisher",
            "fix_agent": "fix_agent",
            "__end__": END,
        },
    )
    graph.add_edge("fix_agent", "coder_agent")
    graph.add_edge("github_publisher", END)

    return graph.compile()


def build_spec_graph():
    """Phase 1: genre_classifier → spec_agent → END"""
    graph = StateGraph(AgentState)
    graph.add_node("genre_classifier", _wrap_node_with_timeout(genre_classifier_node))
    graph.add_node("spec_agent", _wrap_node_with_timeout(spec_agent_node))
    graph.set_entry_point("genre_classifier")
    graph.add_edge("genre_classifier", "spec_agent")
    graph.add_edge("spec_agent", END)
    return graph.compile()


def build_impl_graph():
    """Phase 2: coder_agent → review_guardrails → fix loop → github_publisher → END"""
    graph = StateGraph(AgentState)
    graph.add_node("coder_agent", _wrap_node_with_timeout(coder_agent_node))
    graph.add_node("review_guardrails", _wrap_node_with_timeout(review_guardrails_node))
    graph.add_node("fix_agent", _wrap_node_with_timeout(fix_agent_node))
    graph.add_node("github_publisher", _wrap_node_with_timeout(github_publisher_node))
    graph.set_entry_point("coder_agent")
    graph.add_edge("coder_agent", "review_guardrails")
    graph.add_conditional_edges(
        "review_guardrails",
        route_after_review,
        {
            "github_publisher": "github_publisher",
            "fix_agent": "fix_agent",
            "__end__": END,
        },
    )
    graph.add_edge("fix_agent", "coder_agent")
    graph.add_edge("github_publisher", END)
    return graph.compile()


# モジュール読み込み時にグラフをビルド（invoke 時に使い回す）
_app = None
_spec_app = None
_impl_app = None


def get_graph():
    """コンパイル済みグラフを返す。"""
    global _app
    if _app is None:
        _app = build_graph()
    return _app


def get_spec_graph():
    """Phase 1 コンパイル済みグラフを返す。"""
    global _spec_app
    if _spec_app is None:
        _spec_app = build_spec_graph()
    return _spec_app


def get_impl_graph():
    """Phase 2 コンパイル済みグラフを返す。"""
    global _impl_app
    if _impl_app is None:
        _impl_app = build_impl_graph()
    return _impl_app


def invoke(state: AgentState, *, config: Optional[dict] = None):
    """State を渡してグラフを 1 回実行する。Notion/Supabase 等の外側から呼ぶ。全体タイムアウトあり。"""
    app = get_graph()
    config = config or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.invoke, state, config)
        try:
            return future.result(timeout=TOTAL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            error_logs = list(state.get("error_logs") or [])
            error_logs.append(f"Total timeout ({TOTAL_TIMEOUT_SECONDS}s)")
            return {**state, "error_logs": error_logs, "status": "timeout"}


def invoke_spec(state: AgentState, *, config: Optional[dict] = None):
    """Phase 1 のみ実行: genre_classifier + spec_agent。"""
    app = get_spec_graph()
    config = config or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.invoke, state, config)
        try:
            return future.result(timeout=TOTAL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            error_logs = list(state.get("error_logs") or [])
            error_logs.append(f"Spec phase timeout ({TOTAL_TIMEOUT_SECONDS}s)")
            return {**state, "error_logs": error_logs, "status": "timeout"}


def invoke_impl(state: AgentState, *, config: Optional[dict] = None):
    """Phase 2 のみ実行: coder → review → fix loop → publisher。"""
    app = get_impl_graph()
    config = config or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(app.invoke, state, config)
        try:
            return future.result(timeout=TOTAL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            error_logs = list(state.get("error_logs") or [])
            error_logs.append(f"Impl phase timeout ({TOTAL_TIMEOUT_SECONDS}s)")
            return {**state, "error_logs": error_logs, "status": "timeout"}

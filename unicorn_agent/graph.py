"""LangGraph メインロジック: ノード登録・エッジ・条件エッジでグラフを組み立て。"""
from __future__ import annotations

from typing import Optional
from langgraph.graph import StateGraph, END

from unicorn_agent.state import AgentState
from unicorn_agent.nodes.spec_agent import spec_agent_node
from unicorn_agent.nodes.coder_agent import coder_agent_node
from unicorn_agent.nodes.review_guardrails import review_guardrails_node, route_after_review
from unicorn_agent.nodes.fix_agent import fix_agent_node
from unicorn_agent.nodes.github_publisher import github_publisher_node


def build_graph():
    """StateGraph を組み立ててコンパイルする。"""
    graph = StateGraph(AgentState)

    graph.add_node("spec_agent", spec_agent_node)
    graph.add_node("coder_agent", coder_agent_node)
    graph.add_node("review_guardrails", review_guardrails_node)
    graph.add_node("fix_agent", fix_agent_node)
    graph.add_node("github_publisher", github_publisher_node)

    graph.set_entry_point("spec_agent")
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


# モジュール読み込み時にグラフをビルド（invoke 時に使い回す）
_app = None


def get_graph():
    """コンパイル済みグラフを返す。"""
    global _app
    if _app is None:
        _app = build_graph()
    return _app


def invoke(state: AgentState, *, config: Optional[dict] = None):
    """State を渡してグラフを 1 回実行する。Notion/Supabase 等の外側から呼ぶ。"""
    app = get_graph()
    config = config or {}
    # thread_id を渡すとチェックポイントが有効になる（オプション）
    return app.invoke(state, config=config)

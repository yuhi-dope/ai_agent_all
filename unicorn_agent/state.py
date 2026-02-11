"""LangGraph 用 State 定義。TypedDict で LangChain の add_messages 等と競合しない形にする。"""
from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """エージェントグラフで共有する State。total=False でキーを省略可能に。"""

    user_requirement: str
    spec_markdown: str
    generated_code: dict[str, str]
    error_logs: list[str]
    retry_count: int
    status: str
    fix_instruction: str
    last_error_signature: str
    pr_url: str
    workspace_root: str


def initial_state(
    user_requirement: str,
    workspace_root: str = ".",
) -> AgentState:
    """初期 State を返す。invoke の入力に使う。"""
    return AgentState(
        user_requirement=user_requirement,
        spec_markdown="",
        generated_code={},
        error_logs=[],
        retry_count=0,
        status="started",
        fix_instruction="",
        last_error_signature="",
        pr_url="",
        workspace_root=workspace_root,
    )

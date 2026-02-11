"""LangGraph 用 State 定義。TypedDict で LangChain の add_messages 等と競合しない形にする。"""
from __future__ import annotations

import uuid
from typing import TypedDict


def _default_run_id() -> str:
    return uuid.uuid4().hex[:12]


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
    rules_dir: str
    run_id: str
    output_rules_improvement: bool
    spec_rules_improvement: str
    coder_rules_improvement: str
    review_rules_improvement: str
    fix_rules_improvement: str
    pr_rules_improvement: str


def initial_state(
    user_requirement: str,
    workspace_root: str = ".",
    rules_dir: str = "rules",
    run_id: str | None = None,
    output_rules_improvement: bool = False,
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
        rules_dir=rules_dir,
        run_id=run_id if run_id is not None else _default_run_id(),
        output_rules_improvement=output_rules_improvement,
        spec_rules_improvement="",
        coder_rules_improvement="",
        review_rules_improvement="",
        fix_rules_improvement="",
        pr_rules_improvement="",
    )

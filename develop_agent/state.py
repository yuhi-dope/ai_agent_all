"""LangGraph 用 State 定義。TypedDict で LangChain の add_messages 等と競合しない形にする。"""
from __future__ import annotations

import re
import uuid
from typing import TypedDict


def _default_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _slug_from_requirement(requirement: str, max_len: int = 50) -> str:
    """要件の冒頭からフォルダ名用スラッグを生成。英数字・ハイフン・アンダースコアのみ残し、空なら空文字。"""
    if not (requirement or "").strip():
        return ""
    s = requirement.strip()
    # 英数字・ハイフン・アンダースコア以外をハイフンに
    s = re.sub(r"[^a-zA-Z0-9_\s-]", "-", s)
    s = re.sub(r"[\s]+", "-", s)
    # 連続ハイフンを1つに
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] if s else ""


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
    output_subdir: str
    output_rules_improvement: bool
    genre: str
    spec_rules_improvement: str
    coder_rules_improvement: str
    review_rules_improvement: str
    fix_rules_improvement: str
    pr_rules_improvement: str
    total_input_tokens: int
    total_output_tokens: int


def initial_state(
    user_requirement: str,
    workspace_root: str = ".",
    rules_dir: str = "rules",
    run_id: str | None = None,
    output_rules_improvement: bool = False,
    genre: str | None = None,
) -> AgentState:
    """初期 State を返す。invoke の入力に使う。genre は専門家ジャンル（事務・法務・会計等）。"""
    rid = run_id if run_id is not None else _default_run_id()
    output_folder = _slug_from_requirement(user_requirement) or rid
    output_subdir = f"output/{output_folder}"
    state: AgentState = AgentState(
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
        run_id=rid,
        output_subdir=output_subdir,
        output_rules_improvement=output_rules_improvement,
        spec_rules_improvement="",
        coder_rules_improvement="",
        review_rules_improvement="",
        fix_rules_improvement="",
        pr_rules_improvement="",
        total_input_tokens=0,
        total_output_tokens=0,
    )
    if genre is not None and genre.strip():
        state["genre"] = genre.strip()
    return state

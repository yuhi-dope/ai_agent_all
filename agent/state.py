"""LangGraph 用 State 定義。共通基盤 + コード生成用 + SaaS BPO 用。"""
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


# ── コード生成パイプライン用 State ──────────────────────────

class AgentState(TypedDict, total=False):
    """コード生成パイプラインで共有する State。total=False でキーを省略可能に。"""

    user_requirement: str
    spec_markdown: str
    generated_code: dict[str, str]
    error_logs: list[str]
    retry_count: int
    status: str
    fix_instruction: str
    last_error_signature: str
    workspace_root: str
    rules_dir: str
    run_id: str
    output_subdir: str
    output_rules_improvement: bool
    genre: str
    genre_subcategory: str
    genre_override_reason: str
    spec_rules_improvement: str
    coder_rules_improvement: str
    review_rules_improvement: str
    fix_rules_improvement: str
    publish_rules_improvement: str
    notion_page_id: str
    total_input_tokens: int
    total_output_tokens: int
    sandbox_audit_log: list[dict]
    company_id: str


def initial_state(
    user_requirement: str,
    workspace_root: str = ".",
    rules_dir: str = "rules/develop",
    run_id: str | None = None,
    output_rules_improvement: bool = False,
    genre: str | None = None,
    notion_page_id: str | None = None,
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
        workspace_root=workspace_root,
        rules_dir=rules_dir,
        run_id=rid,
        output_subdir=output_subdir,
        output_rules_improvement=output_rules_improvement,
        spec_rules_improvement="",
        coder_rules_improvement="",
        review_rules_improvement="",
        fix_rules_improvement="",
        publish_rules_improvement="",
        total_input_tokens=0,
        total_output_tokens=0,
        sandbox_audit_log=[],
    )
    if genre is not None and genre.strip():
        state["genre"] = genre.strip()
    if notion_page_id is not None and notion_page_id.strip():
        state["notion_page_id"] = notion_page_id.strip()
    return state


# ── SaaS BPO パイプライン用 State ──────────────────────────

class BPOState(TypedDict, total=False):
    """SaaS BPO パイプラインで共有する State。"""

    # 共通
    run_id: str
    company_id: str
    status: str
    error_logs: list[str]
    total_input_tokens: int
    total_output_tokens: int

    # タスク情報
    task_description: str
    saas_task_id: str
    saas_connection_id: str
    saas_name: str
    genre: str
    rules_dir: str
    dry_run: bool

    # 計画
    saas_available_tools: list[dict]
    saas_operations: list[dict]
    saas_plan_markdown: str

    # 実行結果（サマリーのみ — 企業データは保存しない）
    saas_results: list[dict]
    saas_report_markdown: str

    # 学習システム
    past_failure_warnings: list[str]
    failure_reason: str
    failure_category: str


def initial_bpo_state(
    task_description: str,
    company_id: str,
    saas_connection_id: str,
    saas_name: str,
    genre: str = "",
    run_id: str | None = None,
    dry_run: bool = False,
) -> BPOState:
    """SaaS BPO 用の初期 State を返す。"""
    rid = run_id if run_id is not None else _default_run_id()
    return BPOState(
        run_id=rid,
        company_id=company_id,
        status="planning",
        error_logs=[],
        total_input_tokens=0,
        total_output_tokens=0,
        task_description=task_description,
        saas_task_id="",
        saas_connection_id=saas_connection_id,
        saas_name=saas_name,
        genre=genre,
        rules_dir="rules/saas",
        dry_run=dry_run,
        saas_available_tools=[],
        saas_operations=[],
        saas_plan_markdown="",
        saas_results=[],
        saas_report_markdown="",
        past_failure_warnings=[],
        failure_reason="",
        failure_category="",
    )

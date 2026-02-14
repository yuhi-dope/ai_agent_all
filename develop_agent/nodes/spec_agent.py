"""Spec Agent: 曖昧な自然言語の指示を構造化された Markdown 設計書に変換。Gemini 1.5 Pro 使用。"""

from pathlib import Path

from develop_agent.state import AgentState
from develop_agent.llm.vertex import get_chat_pro
from develop_agent.utils.rule_loader import load_rule
from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_SPEC = """あなたは要件定義の専門家です。ユーザーから渡された曖昧な指示を、開発者が迷わず実装できる「構造化された Markdown 設計書」に変換してください。

進め方: 入力からまず「目的」と「目的を果たすために必要な条件・手段」を抽出し、不足を補足しながら設計書を完成させる。出力の冒頭に「目的」「条件・手段」セクションを必須で含める。

出力は必ず Markdown のみとし、以下のセクションをこの順で含めてください:
- 目的（必須）
- 条件・手段（必須）
- 概要
- 機能要件（箇条書き）
- 非機能要件（任意）
- データ・API の概要（必要な場合）
- 画面・フロー概要（必要な場合）
- 受入条件・テスト観点（推奨）
余計な前置きや説明は書かず、設計書の本文だけを出力してください。"""


def spec_agent_node(state: AgentState) -> dict:
    """user_requirement から spec_markdown を生成する。"""
    req = state.get("user_requirement") or ""
    if not req.strip():
        return {"spec_markdown": "", "status": "spec_done"}

    workspace_root = state.get("workspace_root") or "."
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = Path(workspace_root) / rules_dir_name
    spec_rules = load_rule(rules_dir, "spec_rules", SYSTEM_SPEC)
    stack_domain = load_rule(rules_dir, "stack_domain_rules", "")
    if stack_domain.strip():
        system_prompt = f"## スタック・ドメイン・自社前提\n\n{stack_domain.strip()}\n\n---\n\n{spec_rules}"
    else:
        system_prompt = spec_rules

    llm = get_chat_pro()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=req),
    ]
    response = llm.invoke(messages)
    spec_markdown = response.content if hasattr(response, "content") else str(response)
    spec_markdown = spec_markdown.strip()

    out: dict = {
        "spec_markdown": spec_markdown,
        "status": "spec_done",
    }
    usage = getattr(response, "response_metadata", None) or {}
    usage = usage.get("usage_metadata") or usage
    in_tok = int(usage.get("prompt_token_count") or usage.get("input_tokens") or 0)
    out_tok = int(usage.get("candidates_token_count") or usage.get("output_tokens") or 0)
    out["total_input_tokens"] = (state.get("total_input_tokens") or 0) + in_tok
    out["total_output_tokens"] = (state.get("total_output_tokens") or 0) + out_tok
    if state.get("output_rules_improvement"):
        out["spec_rules_improvement"] = (
            f"# Spec フェーズ 改善・追加ルール案\n\n"
            f"## 今回の要件（要約）\n{req[:500]}\n\n"
            f"## 出力セクション\n概要 / 機能要件 / 非機能要件 / データ・API の概要 / 画面・フロー概要 を含めた。\n\n"
            f"## spec_rules.md への追加推奨\n"
            f"次回同様の要件では、必要に応じて「用語定義」や「受入条件」セクションを追加することを検討してください。\n"
        )
    return out

"""Spec Agent: 曖昧な自然言語の指示を構造化された Markdown 設計書に変換。Gemini 1.5 Pro 使用。"""

from unicorn_agent.state import AgentState
from unicorn_agent.llm.vertex import get_chat_pro
from langchain_core.messages import HumanMessage, SystemMessage

SYSTEM_SPEC = """あなたは要件定義の専門家です。ユーザーから渡された曖昧な指示を、開発者が迷わず実装できる「構造化された Markdown 設計書」に変換してください。
出力は必ず Markdown のみとし、以下のセクションを含めてください:
- 概要
- 機能要件（箇条書き）
- 非機能要件（任意）
- データ・API の概要（必要な場合）
- 画面・フロー概要（必要な場合）
余計な前置きや説明は書かず、設計書の本文だけを出力してください。"""


def spec_agent_node(state: AgentState) -> dict:
    """user_requirement から spec_markdown を生成する。"""
    req = state.get("user_requirement") or ""
    if not req.strip():
        return {"spec_markdown": "", "status": "spec_done"}

    llm = get_chat_pro()
    messages = [
        SystemMessage(content=SYSTEM_SPEC),
        HumanMessage(content=req),
    ]
    response = llm.invoke(messages)
    spec_markdown = response.content if hasattr(response, "content") else str(response)

    return {
        "spec_markdown": spec_markdown.strip(),
        "status": "spec_done",
    }

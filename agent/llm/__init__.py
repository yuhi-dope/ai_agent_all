"""LLM クライアント — LLM_PROVIDER 環境変数でプロバイダを切替。
'gemini'（デフォルト）: Vertex AI Gemini
'claude': Anthropic Claude
"""

import os


def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


def get_chat_pro(**kwargs):
    """設計・レビュー用の高品質 LLM を返す。"""
    if _provider() == "claude":
        from agent.llm.claude import get_chat_pro as _claude_pro

        return _claude_pro(**kwargs)
    from agent.llm.vertex import get_chat_pro as _vertex_pro

    return _vertex_pro(**kwargs)


def get_chat_flash(**kwargs):
    """コーディング・分類用のコスト重視 LLM を返す。"""
    if _provider() == "claude":
        from agent.llm.claude import get_chat_flash as _claude_flash

        return _claude_flash(**kwargs)
    from agent.llm.vertex import get_chat_flash as _vertex_flash

    return _vertex_flash(**kwargs)


__all__ = ["get_chat_pro", "get_chat_flash"]

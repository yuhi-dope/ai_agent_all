"""後方互換用シム: agent.llm から re-export。"""

from agent.llm import get_chat_pro, get_chat_flash  # noqa: F401

__all__ = ["get_chat_pro", "get_chat_flash"]

"""Claude (Anthropic) LLM クライアント。vertex.py と同じインターフェース。
パッケージ未導入時は get_chat_pro / get_chat_flash 呼び出し時のみ ImportError。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

CLAUDE_PRO = "claude-sonnet-4-20250514"
CLAUDE_FLASH = "claude-haiku-35-20241022"

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic  # noqa: F401


def _base_client(**kwargs):
    """共通オプションで Anthropic チャットを生成。使用時にのみ langchain_anthropic を import。"""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:
        raise ImportError(
            "Anthropic 用パッケージが必要です: pip install langchain-anthropic"
        ) from e
    return ChatAnthropic(
        model=kwargs.get("model", CLAUDE_FLASH),
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        temperature=kwargs.get("temperature", 0.2),
        max_tokens=kwargs.get("max_output_tokens", 8192),
    )


def get_chat_pro(**kwargs):
    """設計・レビュー用（Claude Sonnet）。"""
    return _base_client(model=CLAUDE_PRO, **kwargs)


def get_chat_flash(**kwargs):
    """コーディング用（Claude Haiku、コスト重視）。"""
    return _base_client(model=CLAUDE_FLASH, **kwargs)

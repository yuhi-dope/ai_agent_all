"""Vertex AI (Gemini) クライアント。Spec は Pro、Coder は Flash を指定。
パッケージ未導入時は get_chat_pro / get_chat_flash 呼び出し時のみ ImportError。"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

# モデル ID（Vertex AI の表記に合わせる。1.5 系は新規プロジェクトで利用不可のため 2.x 系を使用）
GEMINI_PRO = "gemini-2.5-pro"
GEMINI_FLASH = "gemini-2.0-flash"

if TYPE_CHECKING:
    from langchain_google_vertexai import ChatVertexAI  # noqa: F401


def _base_client(**kwargs):
    """共通オプションで Vertex AI チャットを生成。使用時にのみ langchain_google_vertexai を import。"""
    try:
        from langchain_google_vertexai import ChatVertexAI
    except ImportError as e:
        raise ImportError(
            "Vertex AI 用パッケージが必要です: pip install langchain-google-vertexai google-cloud-aiplatform"
        ) from e
    return ChatVertexAI(
        model=kwargs.get("model", GEMINI_FLASH),
        project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        temperature=kwargs.get("temperature", 0.2),
        max_output_tokens=kwargs.get("max_output_tokens", 8192),
    )


def get_chat_pro(**kwargs):
    """設計・レビュー用（Gemini 1.5 Pro）。"""
    return _base_client(model=GEMINI_PRO, **kwargs)


def get_chat_flash(**kwargs):
    """コーディング用（Gemini 1.5 Flash、コスト重視）。"""
    return _base_client(model=GEMINI_FLASH, **kwargs)

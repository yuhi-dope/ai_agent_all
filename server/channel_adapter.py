"""
マルチチャンネル入力アダプタの抽象基底クラス。
Webhook を ChannelMessage に正規化し、結果をチャンネルに返信するインターフェースを定義する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Request


@dataclass
class ChannelMessage:
    """チャンネル共通のメッセージ構造。"""

    source: str  # "notion" | "slack" | "gdrive" | "chatwork"
    requirement: str  # 要件テキスト
    sender_id: str = ""  # チャンネル固有の送信者 ID
    reply_to: dict = field(default_factory=dict)  # 返信先メタデータ（チャンネル固有）
    genre: Optional[str] = None  # ジャンル指定（任意）
    raw_payload: Optional[dict] = None  # 元の Webhook ペイロード（デバッグ用）


class ChannelAdapter(ABC):
    """チャンネルアダプタの基底クラス。"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """プロバイダ識別子（例: 'slack', 'chatwork'）を返す。"""
        ...

    @abstractmethod
    async def parse_webhook(self, request: Request) -> Optional[ChannelMessage]:
        """
        Webhook リクエストを ChannelMessage にパースする。
        無視すべきイベント（bot 自身のメッセージ等）の場合は None を返す。
        署名検証失敗時は HTTPException を投げる。
        """
        ...

    @abstractmethod
    async def send_progress(self, reply_to: dict, message: str) -> None:
        """処理中の進捗メッセージを送信する。"""
        ...

    @abstractmethod
    async def send_result(
        self, reply_to: dict, run_id: str, status: str, detail: str = ""
    ) -> None:
        """最終結果をチャンネルに送信する。"""
        ...

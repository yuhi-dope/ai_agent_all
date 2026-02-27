"""Streamable HTTP MCP クライアント.

STDIO トランスポートの代替として、HTTP 経由で MCP サーバーと通信する。
SaaS MCP アダプタなど、24h 常時接続が必要なユースケースに使用する。

STDIO (既存):
  - Docker コンテナ内の MCP サーバーと docker exec 経由で通信
  - コンテナ起動→破棄の使い捨てモデルに最適

Streamable HTTP (本モジュール):
  - HTTP 経由で MCP サーバーと通信
  - 長時間稼働するサービス型 MCP サーバー向け
  - 複数クライアントからの同時アクセスが可能
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


class StreamableHTTPMCPClient:
    """Streamable HTTP トランスポートで MCP サーバーに接続するクライアント。

    Usage::

        async with StreamableHTTPMCPClient("http://localhost:8765/mcp") as client:
            tools = await client.list_tools()
            result = await client.call_tool("file_read", {"path": "main.py"})
    """

    def __init__(
        self,
        server_url: str,
        headers: Optional[dict[str, str]] = None,
    ):
        self.server_url = server_url
        self.headers = headers or {}
        self.session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    async def __aenter__(self) -> "StreamableHTTPMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        """MCP サーバーに Streamable HTTP で接続する。"""
        self._exit_stack = AsyncExitStack()

        transport = await self._exit_stack.enter_async_context(
            streamablehttp_client(self.server_url, headers=self.headers)
        )
        read_stream, write_stream, _ = transport

        self.session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()

        tools_response = await self.session.list_tools()
        tool_names = [t.name for t in tools_response.tools]
        logger.info(
            "HTTP MCP connected to %s. Tools: %s",
            self.server_url,
            tool_names,
        )

    async def disconnect(self) -> None:
        """接続を切断する。"""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._exit_stack = None
            self.session = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """利用可能なツール一覧を取得する。"""
        if not self.session:
            raise RuntimeError("Not connected")
        response = await self.session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema if hasattr(t, "inputSchema") else {},
            }
            for t in response.tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """MCP ツールを呼び出す。"""
        if not self.session:
            raise RuntimeError("Not connected")

        result = await self.session.call_tool(name, arguments)

        text_parts = []
        for content_item in result.content:
            if hasattr(content_item, "text"):
                text_parts.append(content_item.text)
        raw_text = "\n".join(text_parts)

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return {"success": False, "raw": raw_text}

    @property
    def is_connected(self) -> bool:
        """接続中かどうか。"""
        return self.session is not None

"""Slack Typed Function Tools."""

from __future__ import annotations

from typing import Any

import httpx

from server.saas.tools.http import SaaSCreds, api_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

register_saas(SaaSMetadata(
    saas_name="slack",
    display_name="Slack",
    genre="communication",
    description="メッセージ送信・チャンネル管理・ファイル共有・リアクション・業務報告通知",
    supported_auth_methods=["oauth2"],
    default_scopes=[
        "chat:write", "channels:read", "channels:history",
        "users:read", "reactions:write", "files:write",
    ],
))

_BASE = "https://slack.com/api"


@saas_tool(saas="slack", genre="communication")
async def slack_send_message(
    channel: str, text: str, blocks: list | None = None, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Slack チャンネルにメッセージを送信する"""
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    return await api_request("POST", f"{_BASE}/chat.postMessage", creds=creds, json=payload)


@saas_tool(saas="slack", genre="communication")
async def slack_list_channels(
    types: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """チャンネル一覧を取得する"""
    params: dict[str, Any] = {}
    if types:
        params["types"] = types
    return await api_request("GET", f"{_BASE}/conversations.list", creds=creds, params=params)


@saas_tool(saas="slack", genre="communication")
async def slack_get_channel_history(
    channel: str, limit: int = 20, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """チャンネルのメッセージ履歴を取得する"""
    return await api_request(
        "GET", f"{_BASE}/conversations.history",
        creds=creds, params={"channel": channel, "limit": limit},
    )


@saas_tool(saas="slack", genre="communication")
async def slack_add_reaction(
    channel: str, timestamp: str, name: str, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """メッセージにリアクションを追加する"""
    return await api_request("POST", f"{_BASE}/reactions.add", creds=creds, json={
        "channel": channel, "timestamp": timestamp, "name": name,
    })


@saas_tool(saas="slack", genre="communication")
async def slack_upload_file(
    channels: str, content: str, filename: str = "file.txt", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Slack にファイルをアップロードする"""
    headers = {"Authorization": f"Bearer {creds.access_token}"}
    data = {"channels": channels, "filename": filename}
    files = {"content": ("file", content.encode())}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{_BASE}/files.uploadV2", headers=headers, data=data, files=files,
        )
        resp.raise_for_status()
        return resp.json()

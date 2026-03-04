"""SaaS ツール共通 HTTP ヘルパー.

各 SaaS ツール関数から使う軽量 HTTP クライアント。
旧 SaaSMCPAdapter._api_request / KintoneAdapter._kintone_request を統合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class SaaSCreds:
    """SaaS 接続に必要な認証情報（旧 SaaSCredentials を簡素化）."""

    access_token: str | None = None
    api_key: str | None = None
    instance_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def api_request(
    method: str,
    url: str,
    *,
    creds: SaaSCreds | None = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """認証ヘッダー付き汎用 HTTP リクエスト."""
    hdrs = dict(headers or {})
    if creds:
        if creds.api_key:
            hdrs.setdefault("X-Cybozu-API-Token", creds.api_key)
        elif creds.access_token:
            hdrs.setdefault("Authorization", f"Bearer {creds.access_token}")
    if method.upper() != "GET":
        hdrs.setdefault("Content-Type", "application/json")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=hdrs, **kwargs)
        if resp.status_code >= 400:
            detail = resp.text[:500] if resp.text else "No detail"
            msg = f"SaaS API {resp.status_code}: {url} - {detail}"
            raise httpx.HTTPStatusError(msg, request=resp.request, response=resp)
        if resp.status_code == 204:
            return {"success": True}
        return resp.json()


async def kintone_request(
    creds: SaaSCreds,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """kintone 専用 HTTP リクエスト（403 の詳細メッセージ付き）."""
    if not creds.instance_url:
        raise ConnectionError("kintone: instance_url が必要です")

    url = f"{creds.instance_url}{path}"
    hdrs = kwargs.pop("headers", {}) or {}
    if method.upper() != "GET":
        hdrs.setdefault("Content-Type", "application/json")
    if creds.api_key:
        hdrs.setdefault("X-Cybozu-API-Token", creds.api_key)
    elif creds.access_token:
        hdrs.setdefault("Authorization", f"Bearer {creds.access_token}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=hdrs, **kwargs)
        if resp.status_code >= 400:
            detail = resp.text[:500] if resp.text else "No detail"
            if resp.status_code == 403:
                msg = (
                    f"kintone API 403 Forbidden: {path} - アクセス権限がありません。"
                    f"対処法: (1) ダッシュボードでkintoneを切断→再接続し、認可画面で全てのスコープを許可してください。"
                    f" (2) kintoneにログインしているアカウントが対象アプリへのアクセス権を持っているか確認してください。"
                    f" Detail: {detail}"
                )
            else:
                msg = f"kintone API {resp.status_code}: {path} - {detail}"
            raise httpx.HTTPStatusError(msg, request=resp.request, response=resp)
        if resp.status_code == 204:
            return {"success": True}
        return resp.json()

"""OAuth トークン自動リフレッシュサービス.

アクティブなSaaS接続のOAuthトークンを定期的にチェックし、
期限切れ前に自動でリフレッシュする。

FastAPI の lifespan で起動:
    @asynccontextmanager
    async def lifespan(app):
        await token_refresh.start()
        yield
        await token_refresh.stop()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# リフレッシュ間隔（秒）: 15分ごとにチェック
REFRESH_INTERVAL_SECONDS = 900

# トークン期限切れの何秒前にリフレッシュするか（5分前）
REFRESH_BUFFER_SECONDS = 300

_task: asyncio.Task | None = None
_running = False

# SaaS ごとのトークンリフレッシュ設定
_TOKEN_ENDPOINTS: dict[str, str] = {
    "salesforce": "https://login.salesforce.com/services/oauth2/token",
    "freee": "https://accounts.secure.freee.co.jp/public_api/token",
    "google_workspace": "https://oauth2.googleapis.com/token",
    # kintone, smarthr は instance_url ベースなので動的に構築
}


async def start() -> None:
    """トークンリフレッシュのバックグラウンドタスクを開始する."""
    global _task, _running
    if _running:
        return
    _running = True
    _task = asyncio.create_task(_refresh_loop())
    logger.info("Token Refresh Service 開始（間隔: %ds）", REFRESH_INTERVAL_SECONDS)


async def stop() -> None:
    """トークンリフレッシュのバックグラウンドタスクを停止する."""
    global _task, _running
    _running = False
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    logger.info("Token Refresh Service 停止")


async def _refresh_loop() -> None:
    """定期的にトークンの有効性をチェックし、必要に応じてリフレッシュする."""
    while _running:
        try:
            await _check_and_refresh_all()
        except Exception:
            logger.exception("トークンリフレッシュチェックでエラー")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _check_and_refresh_all() -> None:
    """全アクティブ接続のトークンをチェックする."""
    from server import oauth_store
from server.saas import connection as saas_connection

    connections = saas_connection.get_active_connections_for_refresh()
    if not connections:
        return

    refreshed = 0
    failed = 0

    for conn in connections:
        saas_name = conn["saas_name"]
        company_id = conn["company_id"]
        connection_id = conn["id"]
        provider = f"saas_{saas_name}"

        token_data = oauth_store.get_token(provider=provider, tenant_id=company_id)
        if not token_data:
            continue

        # トークン期限チェック
        if not _needs_refresh(token_data):
            continue

        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            logger.warning(
                "リフレッシュトークンなし: %s (company=%s)", saas_name, company_id
            )
            saas_connection.update_status(connection_id, "token_expired")
            failed += 1
            continue

        # リフレッシュ実行
        success = await _refresh_token(
            saas_name=saas_name,
            company_id=company_id,
            connection_id=connection_id,
            refresh_token=refresh_token,
            instance_url=conn.get("instance_url"),
        )
        if success:
            refreshed += 1
        else:
            failed += 1

    if refreshed or failed:
        logger.info(
            "トークンリフレッシュ完了: 成功=%d, 失敗=%d", refreshed, failed
        )


def _needs_refresh(token_data: dict) -> bool:
    """トークンがリフレッシュが必要か判定する."""
    expires_at = token_data.get("expires_at")
    if not expires_at:
        return False  # 期限なしトークンはリフレッシュ不要

    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False

    now = datetime.now(timezone.utc)
    remaining = (expires_at - now).total_seconds()
    return remaining < REFRESH_BUFFER_SECONDS


async def _refresh_token(
    saas_name: str,
    company_id: str,
    connection_id: str,
    refresh_token: str,
    instance_url: str | None = None,
) -> bool:
    """OAuthトークンをリフレッシュする."""
    from server import oauth_store
from server.saas import connection as saas_connection
    from server.channel_config import get_config_value

    # トークンエンドポイントを決定
    token_url = _TOKEN_ENDPOINTS.get(saas_name)
    if not token_url and instance_url:
        # kintone, smarthr 等 instance_url ベース
        if saas_name == "kintone":
            token_url = f"{instance_url}/oauth2/token"
        elif saas_name == "smarthr":
            token_url = f"{instance_url}/oauth/token"

    if not token_url:
        logger.warning("トークンエンドポイント不明: %s", saas_name)
        return False

    # クライアント認証情報を取得
    client_id = get_config_value(company_id, saas_name, "client_id")
    client_secret = get_config_value(company_id, saas_name, "client_secret")

    if not client_id or not client_secret:
        logger.warning("OAuth設定不足: %s (company=%s)", saas_name, company_id)
        return False

    # リフレッシュリクエスト送信
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )

        if resp.status_code != 200:
            logger.warning(
                "トークンリフレッシュ失敗: %s (status=%d, body=%s)",
                saas_name, resp.status_code, resp.text[:200],
            )
            saas_connection.update_status(connection_id, "token_expired")
            return False

        data = resp.json()
        new_access_token = data.get("access_token")
        new_refresh_token = data.get("refresh_token", refresh_token)

        # 期限計算
        expires_at = None
        if "expires_in" in data:
            expires_at = datetime.now(timezone.utc).timestamp() + data["expires_in"]
            expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

        # トークン保存
        provider = f"saas_{saas_name}"
        oauth_store.save_token(
            provider=provider,
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            expires_at=expires_at,
            tenant_id=company_id,
            raw_response=data,
        )

        # 接続ステータスを active に更新
        saas_connection.update_status(connection_id, "active")
        logger.info("トークンリフレッシュ成功: %s (company=%s)", saas_name, company_id)
        return True

    except Exception:
        logger.exception("トークンリフレッシュ例外: %s", saas_name)
        saas_connection.update_status(connection_id, "error", "refresh_failed")
        return False


async def refresh_single(connection_id: str, company_id: str) -> bool:
    """手動で1件のトークンをリフレッシュする（API用）."""
    from server import oauth_store
from server.saas import connection as saas_connection

    conn = saas_connection.get_connection(connection_id, company_id=company_id)
    if not conn:
        return False

    saas_name = conn["saas_name"]
    provider = f"saas_{saas_name}"
    token_data = oauth_store.get_token(provider=provider, tenant_id=company_id)
    if not token_data or not token_data.get("refresh_token"):
        return False

    return await _refresh_token(
        saas_name=saas_name,
        company_id=company_id,
        connection_id=connection_id,
        refresh_token=token_data["refresh_token"],
        instance_url=conn.get("instance_url"),
    )

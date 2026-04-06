"""SlackConnector — Slack Web API アダプター。"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class SlackConnector(BaseConnector):
    """Slack Web API コネクタ。

    credentials:
        bot_token (str): Bot User OAuth Token（xoxb-…）
    """

    base_url: str = "https://slack.com/api"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.credentials['bot_token']}",
            "Content-Type": "application/json",
        }

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Slack リソースを取得する。

        Args:
            resource: "channels" | "messages"
            filters:
                channels: 不要
                messages: channel (str), oldest (str, optional)

        Returns:
            レコードの list
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            if resource == "channels":
                resp = await client.get(
                    f"{self.base_url}/conversations.list",
                    headers=self.headers,
                )
                resp.raise_for_status()
                return resp.json().get("channels", [])

            if resource == "messages":
                params: dict = {}
                if "channel" in filters:
                    params["channel"] = filters["channel"]
                if "oldest" in filters:
                    params["oldest"] = filters["oldest"]
                resp = await client.get(
                    f"{self.base_url}/conversations.history",
                    params=params,
                    headers=self.headers,
                )
                resp.raise_for_status()
                return resp.json().get("messages", [])

        return []

    async def write_record(self, resource: str, data: dict) -> dict:
        """Slack にメッセージを送信する。

        Args:
            resource: "message" → chat.postMessage を呼ぶ
            data:     chat.postMessage のペイロード（channel, text 等）

        Returns:
            Slack API レスポンス dict
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat.postMessage",
                json=data,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        """auth.test エンドポイントで疎通確認。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self.base_url}/auth.test",
                    headers=self.headers,
                )
                return resp.json().get("ok", False)
        except Exception:
            return False

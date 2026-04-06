"""FreeeConnector — freee API v1 アダプター。"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class FreeeConnector(BaseConnector):
    """freee API v1 コネクタ。

    credentials:
        access_token (str): OAuth2 アクセストークン
        company_id   (int): freee 事業所 ID
    """

    base_url: str = "https://api.freee.co.jp/api/1"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.credentials['access_token']}",
            "Content-Type": "application/json",
        }

    @property
    def company_id(self) -> int:
        return self.config.credentials["company_id"]

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """freee リソース一覧を取得する。

        Args:
            resource: "deals" | "invoices" | "receipts" | "employees" 等
            filters:  クエリパラメータ（company_id は自動付与）

        Returns:
            レコードの list
        """
        params = {"company_id": self.company_id, **filters}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}/{resource}",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            # freee API はリソース名のキーでリストが入る場合と data キーの場合がある
            return data.get(resource, data.get("data", [data]))

    async def write_record(self, resource: str, data: dict) -> dict:
        """freee リソースを新規作成する。

        Args:
            resource: "deals" | "invoices" 等
            data:     リクエストボディ（company_id は自動付与）

        Returns:
            freee API レスポンス dict
        """
        payload = {"company_id": self.company_id, **data}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/{resource}",
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        """事業所情報エンドポイントへのアクセスで疎通確認。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/companies/{self.company_id}",
                    headers=self.headers,
                )
                return resp.status_code == 200
        except Exception:
            return False

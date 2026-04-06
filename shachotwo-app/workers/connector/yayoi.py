"""YayoiConnector — 弥生会計オンライン API v1 アダプター。"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

_WRITABLE_RESOURCES = {"journal_entries"}
_READABLE_RESOURCES = {"sales", "expenses", "journal_entries"}


class YayoiConnector(BaseConnector):
    """弥生会計オンライン API v1 コネクタ。

    credentials:
        access_token (str): OAuth2 アクセストークン
        company_id   (str): 弥生会計オンライン 事業所 ID
    """

    base_url: str = "https://yayoi-kaikei.jp/api/v1"

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.credentials['access_token']}",
            "Content-Type": "application/json",
        }

    @property
    def company_id(self) -> str:
        return self.config.credentials["company_id"]

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """弥生会計 リソース一覧を取得する。

        Args:
            resource: "sales" | "expenses" | "journal_entries"
            filters:  クエリパラメータ（company_id は自動付与）

        Returns:
            レコードの list

        Raises:
            ValueError: 未対応リソースを指定した場合
        """
        if resource not in _READABLE_RESOURCES:
            raise ValueError(
                f"Unknown resource: {resource}. "
                f"Available: {list(_READABLE_RESOURCES)}"
            )
        params = {"company_id": self.company_id, **filters}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.base_url}/{resource}",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            # 弥生 API はリソース名キー or data キーでリストが入る
            return data.get(resource, data.get("data", [data]))

    async def write_record(self, resource: str, data: dict) -> dict:
        """弥生会計 リソースを新規作成する。

        Args:
            resource: "journal_entries"
            data:     リクエストボディ（company_id は自動付与）

        Returns:
            弥生会計 API レスポンス dict

        Raises:
            ValueError: write 非対応リソースを指定した場合
        """
        if resource not in _WRITABLE_RESOURCES:
            raise ValueError(
                f"write_record は {_WRITABLE_RESOURCES} のみ対応しています。"
                f"指定されたリソース: {resource}"
            )
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

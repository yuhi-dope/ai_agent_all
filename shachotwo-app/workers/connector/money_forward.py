"""MoneyForwardConnector — MoneyForward ME for Business API v3 アダプター。"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

_INVOICE_BASE = "https://invoice.moneyforward.com/api/v3"
_EXPENSE_BASE = "https://expense.moneyforward.com/api/v3"

_RESOURCE_BASE: dict[str, str] = {
    "invoices": _INVOICE_BASE,
    "expenses": _EXPENSE_BASE,
    "journal_entries": _EXPENSE_BASE,
}


class MoneyForwardConnector(BaseConnector):
    """MoneyForward ME for Business API v3 コネクタ。

    credentials:
        access_token (str): OAuth2 アクセストークン
        office_id    (str): 事業所 ID
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.credentials['access_token']}",
            "Content-Type": "application/json",
        }

    @property
    def office_id(self) -> str:
        return self.config.credentials["office_id"]

    def _base_url(self, resource: str) -> str:
        base = _RESOURCE_BASE.get(resource)
        if base is None:
            raise ValueError(
                f"Unknown resource: {resource}. "
                f"Available: {list(_RESOURCE_BASE)}"
            )
        return base

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """MoneyForward リソース一覧を取得する。

        Args:
            resource: "invoices" | "expenses" | "journal_entries"
            filters:  クエリパラメータ（office_id は自動付与）

        Returns:
            レコードの list
        """
        base = self._base_url(resource)
        params = {"office_id": self.office_id, **filters}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base}/{resource}",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
            # MoneyForward API はリソース名キー or data キーでリストが入る
            return data.get(resource, data.get("data", [data]))

    async def write_record(self, resource: str, data: dict) -> dict:
        """MoneyForward リソースを新規作成する。

        Args:
            resource: "invoices"
            data:     リクエストボディ（office_id は自動付与）

        Returns:
            MoneyForward API レスポンス dict

        Raises:
            ValueError: write 非対応リソースを指定した場合
        """
        _WRITABLE = {"invoices"}
        if resource not in _WRITABLE:
            raise ValueError(
                f"write_record は {_WRITABLE} のみ対応しています。"
                f"指定されたリソース: {resource}"
            )
        base = self._base_url(resource)
        payload = {"office_id": self.office_id, **data}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base}/{resource}",
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
                    f"{_INVOICE_BASE}/offices/{self.office_id}",
                    headers=self.headers,
                )
                return resp.status_code == 200
        except Exception:
            return False

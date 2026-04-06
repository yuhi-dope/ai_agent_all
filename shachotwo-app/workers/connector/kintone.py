"""KintoneConnector — kintone REST API v1 アダプター。"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

# kintone レコード取得 API の 1 リクエストあたりの上限
KINTONE_MAX_LIMIT = 500
# 大量取得時のタイムアウト（秒）
KINTONE_READ_TIMEOUT = 60.0


class KintoneConnector(BaseConnector):
    """kintone REST API v1 コネクタ。

    credentials:
        subdomain (str): kintone サブドメイン（例: "mycompany"）
        api_token (str): アプリ API トークン

    base_url: https://{subdomain}.cybozu.com/k/v1
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def base_url(self) -> str:
        return f"https://{self.config.credentials['subdomain']}.cybozu.com/k/v1"

    @property
    def headers(self) -> dict[str, str]:
        token = self.config.credentials.get("api_token", "")
        return {
            "X-Cybozu-API-Token": token,
            "Content-Type": "application/json",
        }

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """kintone レコード一覧を取得する。

        Args:
            resource: app_id（文字列または数値）
            filters:  query キーに kintone クエリ文字列を指定可能

        Returns:
            レコードの list
        """
        params: dict = {"app": resource}
        if "query" in filters:
            params["query"] = filters["query"]
        async with httpx.AsyncClient(timeout=KINTONE_READ_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}/records.json",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("records", [])

    async def read_records_page(
        self,
        resource: str,
        *,
        query: str,
        limit: int = KINTONE_MAX_LIMIT,
    ) -> list[dict]:
        """レコードを 1 ページ取得する。query に order by を含めること。

        limit は最大 500。offset 10,000 件の制限を避けるため大量取得は $id 条件の query と併用する。
        """
        lim = max(1, min(limit, KINTONE_MAX_LIMIT))
        params: dict = {"app": resource, "query": query, "limit": lim}
        async with httpx.AsyncClient(timeout=KINTONE_READ_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}/records.json",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("records", [])

    async def list_apps(self) -> list[dict]:
        """GET /k/v1/apps.json — トークンがアクセス可能なアプリ一覧。"""
        async with httpx.AsyncClient(timeout=KINTONE_READ_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}/apps.json",
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
        out: list[dict] = []
        for a in data.get("apps", []) or []:
            aid = a.get("appId", a.get("id"))
            if aid is None:
                continue
            out.append({
                "appId": str(aid),
                "name": a.get("name", "") or "",
                "spaceId": a.get("spaceId"),
            })
        return out

    async def list_form_fields(self, app_id: str) -> list[dict]:
        """GET /k/v1/app/form/fields.json — フォームフィールド定義をフラット配列で返す。"""
        params = {"app": app_id}
        async with httpx.AsyncClient(timeout=KINTONE_READ_TIMEOUT) as client:
            resp = await client.get(
                f"{self.base_url}/app/form/fields.json",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()
        props = data.get("properties") or {}
        fields: list[dict] = []
        if isinstance(props, dict):
            for code, prop in props.items():
                if not isinstance(prop, dict):
                    continue
                fields.append({
                    "code": prop.get("code", code),
                    "label": prop.get("label", "") or "",
                    "type": prop.get("type", "") or "",
                    "required": bool(prop.get("required", False)),
                })
        return fields

    async def write_record(self, resource: str, data: dict) -> dict:
        """kintone レコードを新規作成または更新する。

        id キーがあれば PUT（更新）、なければ POST（新規）。

        Args:
            resource: app_id
            data:     レコードデータ。id を含む場合は更新

        Returns:
            kintone API レスポンス dict
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            if "id" in data:
                record_id = data.pop("id")
                payload = {"app": resource, "id": record_id, "record": data}
                resp = await client.put(
                    f"{self.base_url}/record.json",
                    json=payload,
                    headers=self.headers,
                )
            else:
                payload = {"app": resource, "record": data}
                resp = await client.post(
                    f"{self.base_url}/record.json",
                    json=payload,
                    headers=self.headers,
                )
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        """app.json エンドポイントへのアクセスで疎通確認。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/app.json",
                    params={"id": 1},
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

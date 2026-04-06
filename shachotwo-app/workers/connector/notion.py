"""NotionConnector — Notion API アダプター。

credentials:
    integration_token (str): Notion インテグレーショントークン

API docs: https://developers.notion.com/reference/intro
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class NotionConnector(BaseConnector):
    """Notion API コネクタ。

    credentials:
        integration_token (str): Notion インテグレーショントークン

    対応リソース（read_records）:
        "databases/{database_id}/query" — データベースクエリ
        "search"                        — 全文検索

    対応リソース（write_record）:
        "pages" — ページ作成
    """

    BASE_URL: str = "https://api.notion.com/v1"
    NOTION_VERSION: str = "2022-06-28"
    _TIMEOUT: float = 10.0
    _HEALTH_TIMEOUT: float = 5.0
    _RATE_LIMIT_STATUS: int = 429
    _AUTH_ERROR_STATUSES: frozenset[int] = frozenset({401, 403})

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        token = self.config.credentials.get("integration_token", "")
        return {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """エラーレスポンスを適切な例外に変換する。

        Raises:
            PermissionError: 認証エラー（401/403）
            RuntimeError:    レート制限（429）
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        if resp.status_code in self._AUTH_ERROR_STATUSES:
            raise PermissionError(
                f"Notion 認証エラー (HTTP {resp.status_code}): "
                "integration_token が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"Notion レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Notion からレコードを読み取る。

        Args:
            resource: リソース識別子
                "databases/{database_id}/query" — DB クエリ（POST）
                "search"                        — 全文検索（POST）
            filters:
                databases/{id}/query の場合:
                    filter     (dict): Notion フィルター条件
                    sorts      (list): ソート条件
                    page_size  (int):  取得件数（最大 100）
                    start_cursor (str): ページネーションカーソル
                search の場合:
                    query      (str):  検索キーワード
                    filter     (dict): object タイプフィルター
                    page_size  (int):  取得件数

        Returns:
            レコードの list（Notion の results 配列）

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                # databases/{id}/query と search は POST で body を渡す
                resp = await client.post(
                    f"{self.BASE_URL}/{resource}",
                    json=filters,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                # Notion API は {"results": [...], "next_cursor": ...} 形式で返す
                return data.get("results", [data])
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Notion API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """Notion にレコードを書き込む（ページ作成）。

        Args:
            resource: リソース識別子
                "pages" — ページ作成
            data: 書き込むデータ。
                parent     (dict, 必須): 親オブジェクト
                    {"database_id": "..."} または {"page_id": "..."}
                properties (dict, 必須): ページプロパティ
                children   (list, 任意): ページコンテンツブロック

        Returns:
            Notion API レスポンス dict（作成されたページオブジェクト）

        Raises:
            ValueError: write 非対応リソースを指定した場合
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        _WRITABLE = {"pages"}
        if resource not in _WRITABLE:
            raise ValueError(
                f"write_record は {_WRITABLE} のみ対応しています。"
                f"指定されたリソース: {resource}"
            )

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/{resource}",
                    json=data,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                return resp.json()
        except (PermissionError, RuntimeError, ValueError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Notion API タイムアウト: resource={resource}"
            )

    async def health_check(self) -> bool:
        """/users/me エンドポイントへのアクセスで API 疎通確認。

        Returns:
            True: 正常接続（HTTP 2xx〜4xx）
            False: 接続失敗・サーバーエラー・例外発生
        """
        try:
            async with httpx.AsyncClient(timeout=self._HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/users/me",
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

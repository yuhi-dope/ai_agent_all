"""BacklogConnector — Backlog タスク管理 API アダプター。

credentials:
    api_key   (str): Backlog API キー
    space_key (str): スペースキー（例: "myspace.backlog.com" の "myspace" 部分）

API docs: https://developer.nulab.com/ja/docs/backlog/
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class BacklogConnector(BaseConnector):
    """Backlog API v2 コネクタ。

    credentials:
        api_key   (str): Backlog API キー
        space_key (str): スペースキー

    対応リソース（read_records）:
        "issues"   — 課題一覧
        "projects" — プロジェクト一覧

    対応リソース（write_record）:
        "issues" — 課題作成

    認証方式:
        Authorization ヘッダー不要。apiKey クエリパラメータで認証する。
    """

    _TIMEOUT: float = 10.0
    _HEALTH_TIMEOUT: float = 5.0
    _RATE_LIMIT_STATUS: int = 429
    _AUTH_ERROR_STATUSES: frozenset[int] = frozenset({401, 403})

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def base_url(self) -> str:
        space_key = self.config.credentials.get("space_key", "")
        return f"https://{space_key}.backlog.com/api/v2"

    @property
    def api_key(self) -> str:
        return self.config.credentials.get("api_key", "")

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _with_api_key(self, params: dict) -> dict:
        """apiKey をクエリパラメータに付与する。"""
        return {"apiKey": self.api_key, **params}

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """エラーレスポンスを適切な例外に変換する。

        Raises:
            PermissionError: 認証エラー（401/403）
            RuntimeError:    レート制限（429）
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        if resp.status_code in self._AUTH_ERROR_STATUSES:
            raise PermissionError(
                f"Backlog 認証エラー (HTTP {resp.status_code}): "
                "api_key が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"Backlog レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Backlog からレコードを読み取る。

        Args:
            resource: リソース識別子
                "issues"   — 課題一覧
                "projects" — プロジェクト一覧
            filters:
                projectId[]   (int|list): プロジェクト ID（issues のみ）
                statusId[]    (int|list): ステータス ID（issues のみ）
                assigneeId[]  (int|list): 担当者 ID（issues のみ）
                keyword       (str):      キーワード検索
                count         (int):      取得件数（最大 100）
                offset        (int):      オフセット
                その他キーはそのままクエリパラメータとして付与

        Returns:
            レコードの list

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        params = self._with_api_key(filters)

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/{resource}",
                    params=params,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                if isinstance(data, list):
                    return data
                if resource in data:
                    return data[resource]
                return [data]
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Backlog API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """Backlog にレコードを書き込む（課題作成）。

        Args:
            resource: リソース識別子
                "issues" — 課題作成
            data: 書き込むデータ。
                projectId   (int, 必須): プロジェクト ID
                summary     (str, 必須): 課題の件名
                issueTypeId (int, 必須): 課題種別 ID
                priorityId  (int, 必須): 優先度 ID
                その他 Backlog API の課題追加パラメータ

        Returns:
            Backlog API レスポンス dict

        Raises:
            ValueError: write 非対応リソースを指定した場合
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        _WRITABLE = {"issues"}
        if resource not in _WRITABLE:
            raise ValueError(
                f"write_record は {_WRITABLE} のみ対応しています。"
                f"指定されたリソース: {resource}"
            )
        params = {"apiKey": self.api_key}

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/{resource}",
                    params=params,
                    json=data,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                return resp.json()
        except (PermissionError, RuntimeError, ValueError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Backlog API タイムアウト: resource={resource}"
            )

    async def health_check(self) -> bool:
        """プロジェクト一覧エンドポイントへのアクセスで API 疎通確認。

        Returns:
            True: 正常接続（HTTP 2xx〜4xx）
            False: 接続失敗・サーバーエラー・例外発生
        """
        try:
            async with httpx.AsyncClient(timeout=self._HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/projects",
                    params={"apiKey": self.api_key},
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

"""JobcanConnector — ジョブカン勤怠管理 API アダプター。

credentials:
    access_token (str): ジョブカン API アクセストークン
    company_id   (str): 企業 ID（ジョブカン管理画面で確認）

API docs: https://developer.jobcan.jp/
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class JobcanConnector(BaseConnector):
    """ジョブカン勤怠管理 API コネクタ。

    credentials:
        access_token (str): API アクセストークン
        company_id   (str): 企業 ID

    対応リソース（read_records）:
        "attendance_records" — 勤怠記録
        "employees"          — 従業員一覧

    対応リソース（write_record）:
        "attendance_records" — 打刻修正
    """

    BASE_URL: str = "https://ssl.jobcan.jp/api/staff"
    _TIMEOUT: float = 10.0
    _HEALTH_TIMEOUT: float = 5.0
    _RATE_LIMIT_STATUS: int = 429
    _AUTH_ERROR_STATUSES: frozenset[int] = frozenset({401, 403})

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def headers(self) -> dict[str, str]:
        token = self.config.credentials.get("access_token", "")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @property
    def company_id(self) -> str:
        return self.config.credentials.get("company_id", "")

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """エラーレスポンスを適切な例外に変換する。

        Raises:
            PermissionError: 認証エラー（401/403）
            RuntimeError:    レート制限（429）
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        if resp.status_code in self._AUTH_ERROR_STATUSES:
            raise PermissionError(
                f"ジョブカン認証エラー (HTTP {resp.status_code}): "
                "access_token が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"ジョブカン レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """ジョブカン からレコードを読み取る。

        Args:
            resource: リソース識別子
                "attendance_records" — 勤怠記録
                "employees"          — 従業員一覧
            filters:
                start_date   (str): 開始日 (YYYY-MM-DD)
                end_date     (str): 終了日 (YYYY-MM-DD)
                employee_id  (str): 従業員 ID
                その他キーはそのままクエリパラメータとして付与

        Returns:
            レコードの list

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        params: dict = {"company_id": self.company_id, **filters}

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/{resource}",
                    params=params,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                if isinstance(data, list):
                    return data
                if resource in data:
                    return data[resource]
                return data.get("data", [data])
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"ジョブカン API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """ジョブカン にレコードを書き込む（打刻修正等）。

        id キーがあれば PUT（更新）、なければ POST（新規登録）。

        Args:
            resource: リソース識別子
                "attendance_records" — 打刻修正
            data: 書き込むデータ。
                "id" キーがある場合は PUT（更新）として扱い、
                エンドポイントに /id を付与する。
                company_id は自動付与。

        Returns:
            ジョブカン API レスポンス dict

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        _WRITABLE = {"attendance_records"}
        if resource not in _WRITABLE:
            raise ValueError(
                f"write_record は {_WRITABLE} のみ対応しています。"
                f"指定されたリソース: {resource}"
            )
        payload: dict = {"company_id": self.company_id, **data}

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                if "id" in payload:
                    record_id = payload.pop("id")
                    resp = await client.put(
                        f"{self.BASE_URL}/{resource}/{record_id}",
                        json=payload,
                        headers=self.headers,
                    )
                else:
                    resp = await client.post(
                        f"{self.BASE_URL}/{resource}",
                        json=payload,
                        headers=self.headers,
                    )
                self._handle_error_response(resp)
                return resp.json()
        except (PermissionError, RuntimeError, ValueError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"ジョブカン API タイムアウト: resource={resource}"
            )

    async def health_check(self) -> bool:
        """勤怠記録エンドポイントへのアクセスで API 疎通確認。

        Returns:
            True: 正常接続（HTTP 2xx〜4xx）
            False: 接続失敗・サーバーエラー・例外発生
        """
        try:
            async with httpx.AsyncClient(timeout=self._HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/attendance_records",
                    params={"company_id": self.company_id},
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

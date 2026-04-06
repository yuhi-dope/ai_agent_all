"""Microsoft365Connector — Microsoft 365（Teams + Outlook）API アダプター。

credentials:
    access_token (str): OAuth2 アクセストークン
    tenant_id    (str): Azure AD テナント ID

API docs: https://learn.microsoft.com/ja-jp/graph/overview
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig


class Microsoft365Connector(BaseConnector):
    """Microsoft Graph API コネクタ（Teams + Outlook）。

    credentials:
        access_token (str): OAuth2 アクセストークン
        tenant_id    (str): Azure AD テナント ID

    対応リソース（read_records）:
        "me/messages"    — メール一覧（Outlook）
        "me/events"      — カレンダーイベント一覧
        "me/joinedTeams" — 参加中の Teams チーム一覧

    対応リソース（write_record）:
        "me/messages/send" — メール送信
    """

    BASE_URL: str = "https://graph.microsoft.com/v1.0"
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
    def tenant_id(self) -> str:
        return self.config.credentials.get("tenant_id", "")

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """エラーレスポンスを適切な例外に変換する。

        Raises:
            PermissionError: 認証エラー（401/403）
            RuntimeError:    レート制限（429）
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        if resp.status_code in self._AUTH_ERROR_STATUSES:
            raise PermissionError(
                f"Microsoft 365 認証エラー (HTTP {resp.status_code}): "
                "access_token が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"Microsoft 365 レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Microsoft Graph API からレコードを読み取る。

        Args:
            resource: リソース識別子
                "me/messages"    — メール一覧（Outlook）
                "me/events"      — カレンダーイベント一覧
                "me/joinedTeams" — 参加中の Teams チーム一覧
            filters:
                $top     (int):  取得件数（最大 999）
                $skip    (int):  オフセット
                $filter  (str):  OData フィルター式
                $orderby (str):  ソート条件
                $select  (str):  取得フィールドの絞り込み
                その他キーはそのままクエリパラメータとして付与

        Returns:
            レコードの list（Graph API の value 配列）

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/{resource}",
                    params=filters,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                # Microsoft Graph API は {"value": [...], "@odata.nextLink": ...} 形式
                if isinstance(data, list):
                    return data
                return data.get("value", [data])
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Microsoft 365 API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """Microsoft Graph API にリクエストを送信する（メール送信等）。

        Args:
            resource: リソース識別子
                "me/messages/send" — メール送信
            data: 送信データ。
                me/messages/send の場合:
                    message (dict, 必須):
                        subject      (str):  件名
                        body         (dict): 本文 {"contentType": "HTML"|"Text", "content": "..."}
                        toRecipients (list): 宛先 [{"emailAddress": {"address": "..."}}]

        Returns:
            Microsoft Graph API レスポンス dict
            （me/messages/send は 202 Accepted で空レスポンスの場合は {} を返す）

        Raises:
            ValueError: write 非対応リソースを指定した場合
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        _WRITABLE = {"me/messages/send"}
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
                # sendMail は 202 Accepted で空ボディの場合がある
                if resp.status_code == 202 or not resp.content:
                    return {}
                return resp.json()
        except (PermissionError, RuntimeError, ValueError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Microsoft 365 API タイムアウト: resource={resource}"
            )

    async def health_check(self) -> bool:
        """/me エンドポイントへのアクセスで API 疎通確認。

        Returns:
            True: 正常接続（HTTP 2xx〜4xx）
            False: 接続失敗・サーバーエラー・例外発生
        """
        try:
            async with httpx.AsyncClient(timeout=self._HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/me",
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

"""SmartHRConnector — 従業員マスタ・社保手続き連携。

credentials:
    access_token (str): SmartHR APIアクセストークン
    subdomain    (str): SmartHRサブドメイン（例: "mycompany"）
                        指定がない場合は BASE_URL をそのまま使用

SmartHR API docs: https://developer.smarthr.jp/api/
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

# 従業員データの性別コード（SmartHR → 共通形式）
_GENDER_MAP: dict[str, str] = {
    "male": "男性",
    "female": "女性",
    "not_declared": "未申告",
}

# 在籍ステータス（SmartHR → 共通形式）
_EMPLOYMENT_STATUS_MAP: dict[str, str] = {
    "employed": "在籍",
    "retired": "退職",
    "resigned": "退職",
    "suspended": "休職",
}


class SmartHRConnector(BaseConnector):
    """SmartHR API v1 コネクタ。

    credentials:
        access_token (str): SmartHR APIアクセストークン
        subdomain    (str): SmartHRサブドメイン（オプション）
                            例: "mycompany" → https://mycompany.smarthr.jp/api/v1
                            省略時は https://api.smarthr.jp/api/v1 を使用

    対応リソース（read_records）:
        "employees"                  — 従業員一覧
        "employees/{id}"             — 従業員詳細
        "departments"                — 部署一覧
        "employment_types"           — 雇用形態一覧
        "dependents/{employee_id}"   — 扶養家族

    対応リソース（write_record）:
        "employees"                  — 従業員登録
        "dependents/{employee_id}"   — 扶養家族登録
    """

    _DEFAULT_BASE_URL: str = "https://api.smarthr.jp/api/v1"
    _TIMEOUT: float = 10.0
    _HEALTH_TIMEOUT: float = 5.0
    _RATE_LIMIT_STATUS: int = 429
    _AUTH_ERROR_STATUSES: frozenset[int] = frozenset({401, 403})

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def base_url(self) -> str:
        subdomain = self.config.credentials.get("subdomain")
        if subdomain:
            return f"https://{subdomain}.smarthr.jp/api/v1"
        return self._DEFAULT_BASE_URL

    @property
    def headers(self) -> dict[str, str]:
        token = self.config.credentials.get("access_token", "")
        return {
            "Authorization": f"Bearer {token}",
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
                f"SmartHR 認証エラー (HTTP {resp.status_code}): "
                "access_token が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"SmartHR レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """SmartHR からレコードを読み取る。

        Args:
            resource: リソース識別子
                "employees"                — 従業員一覧
                "employees/{id}"           — 従業員詳細（リストに包んで返す）
                "departments"              — 部署一覧
                "employment_types"         — 雇用形態一覧
                "dependents/{employee_id}" — 扶養家族一覧
            filters:
                page     (int): ページ番号（デフォルト 1）
                per_page (int): 1ページあたり件数（最大 100）
                その他キーはそのままクエリパラメータとして付与

        Returns:
            レコードの list

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        params: dict = {k: v for k, v in filters.items()}

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/{resource}",
                    params=params,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                # 単一オブジェクト（詳細取得）の場合はリストに包んで返す
                if isinstance(data, list):
                    return data
                return [data]
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"SmartHR API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """SmartHR にレコードを書き込む。

        id キーがあれば PATCH（部分更新）、なければ POST（新規登録）。

        Args:
            resource: リソース識別子
                "employees"                — 従業員登録
                "employees/{id}"           — 従業員更新（resource にIDを含む場合）
                "dependents/{employee_id}" — 扶養家族登録
            data: 書き込むデータ。
                "id" キーがある場合は PATCH（更新）として扱い、
                エンドポイントに /id を付与する。

        Returns:
            SmartHR API レスポンス dict

        Raises:
            PermissionError: 認証エラー
            RuntimeError:    レート制限超過
            httpx.TimeoutException: タイムアウト
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                if "id" in data:
                    record_id = data.pop("id")
                    resp = await client.patch(
                        f"{self.base_url}/{resource}/{record_id}",
                        json=data,
                        headers=self.headers,
                    )
                else:
                    resp = await client.post(
                        f"{self.base_url}/{resource}",
                        json=data,
                        headers=self.headers,
                    )
                self._handle_error_response(resp)
                return resp.json()
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"SmartHR API タイムアウト: resource={resource}"
            )

    async def health_check(self) -> bool:
        """従業員一覧エンドポイントへのアクセスで API 疎通確認。

        Returns:
            True: 正常接続（HTTP 2xx）
            False: 接続失敗・例外発生
        """
        try:
            async with httpx.AsyncClient(timeout=self._HEALTH_TIMEOUT) as client:
                resp = await client.get(
                    f"{self.base_url}/employees",
                    params={"per_page": 1},
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

    @staticmethod
    def map_to_employee_data(smarthr_data: dict) -> dict:
        """SmartHR レスポンスを共通従業員データ形式にマッピング。

        Args:
            smarthr_data: SmartHR API の従業員オブジェクト

        Returns:
            共通従業員データ形式:
            {
                "employee_id":     str   — SmartHR の emp_code（社員番号）
                "name":            str   — 氏名（姓名結合）
                "name_kana":       str   — 氏名カナ
                "email":           str   — メールアドレス
                "department":      str   — 部署名
                "employment_type": str   — 雇用形態
                "employment_status": str — 在籍ステータス（日本語）
                "joined_at":       str   — 入社日 (YYYY-MM-DD)
                "resigned_at":     str|None — 退職日
                "gender":          str   — 性別（日本語）
                "birth_date":      str   — 生年月日 (YYYY-MM-DD)
            }
        """
        last_name = smarthr_data.get("last_name", "")
        first_name = smarthr_data.get("first_name", "")
        last_name_kana = smarthr_data.get("last_name_kana", "")
        first_name_kana = smarthr_data.get("first_name_kana", "")

        dept = smarthr_data.get("department") or {}
        emp_type = smarthr_data.get("employment_type") or {}

        gender_raw = smarthr_data.get("gender", "")
        status_raw = smarthr_data.get("employment_status", "employed")

        return {
            "employee_id": smarthr_data.get("emp_code", ""),
            "name": f"{last_name} {first_name}".strip(),
            "name_kana": f"{last_name_kana} {first_name_kana}".strip(),
            "email": smarthr_data.get("email", ""),
            "department": dept.get("name", "") if isinstance(dept, dict) else "",
            "employment_type": emp_type.get("name", "") if isinstance(emp_type, dict) else "",
            "employment_status": _EMPLOYMENT_STATUS_MAP.get(status_raw, status_raw),
            "joined_at": smarthr_data.get("entered_at", ""),
            "resigned_at": smarthr_data.get("resigned_at"),
            "gender": _GENDER_MAP.get(gender_raw, gender_raw),
            "birth_date": smarthr_data.get("birth_date", ""),
        }

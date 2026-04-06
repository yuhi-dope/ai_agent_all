"""KingOfTimeConnector — 勤怠データ連携。

credentials:
    access_token (str): KING OF TIME APIアクセストークン

API docs: https://developer.kingtime.jp/
"""
import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

# 勤怠区分コード（KING OF TIME → 共通形式）
_ATTENDANCE_TYPE_MAP: dict[str, str] = {
    "normal": "通常",
    "overtime": "残業",
    "holiday_work": "休日出勤",
    "paid_leave": "有給休暇",
    "absence": "欠勤",
    "late": "遅刻",
    "early_leave": "早退",
}


class KingOfTimeConnector(BaseConnector):
    """KING OF TIME API コネクタ。

    credentials:
        access_token (str): KING OF TIME APIアクセストークン

    対応リソース（read_records）:
        "daily_workings"    — 日次勤怠データ
        "monthly_workings"  — 月次勤怠サマリー
        "employees"         — 従業員一覧
        "divisions"         — 所属一覧
        "timerecords"       — 打刻データ

    filters（共通）:
        date              (str): 対象日 (YYYY-MM-DD)
        start_date        (str): 開始日 (YYYY-MM-DD)
        end_date          (str): 終了日 (YYYY-MM-DD)
        employee_key      (str): 従業員キー
        division_code     (str): 所属コード

    対応リソース（write_record）:
        "timerecords"       — 打刻データ登録・修正
    """

    BASE_URL: str = "https://api.kingtime.jp/v1"
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

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """エラーレスポンスを適切な例外に変換する。

        Raises:
            PermissionError: 認証エラー（401/403）
            RuntimeError:    レート制限（429）
            httpx.HTTPStatusError: その他の HTTP エラー
        """
        if resp.status_code in self._AUTH_ERROR_STATUSES:
            raise PermissionError(
                f"KING OF TIME 認証エラー (HTTP {resp.status_code}): "
                "access_token が無効または期限切れです"
            )
        if resp.status_code == self._RATE_LIMIT_STATUS:
            retry_after = resp.headers.get("Retry-After", "不明")
            raise RuntimeError(
                f"KING OF TIME レート制限超過 (HTTP 429): "
                f"Retry-After={retry_after}秒後に再試行してください"
            )
        resp.raise_for_status()

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """KING OF TIME からレコードを読み取る。

        Args:
            resource: リソース識別子
                "daily_workings"   — 日次勤怠データ
                "monthly_workings" — 月次勤怠サマリー
                "employees"        — 従業員一覧
                "divisions"        — 所属一覧
                "timerecords"      — 打刻データ
            filters:
                date          (str): 対象日 (YYYY-MM-DD)
                start_date    (str): 開始日 (YYYY-MM-DD)
                end_date      (str): 終了日 (YYYY-MM-DD)
                employee_key  (str): 従業員キー
                division_code (str): 所属コード
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
                    f"{self.BASE_URL}/{resource}",
                    params=params,
                    headers=self.headers,
                )
                self._handle_error_response(resp)
                data = resp.json()
                # レスポンスがリストの場合はそのまま、辞書の場合は適切なキーを探す
                if isinstance(data, list):
                    return data
                # KING OF TIME API はリソース名のキーでリストが入ることがある
                if resource in data:
                    return data[resource]
                return [data]
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"KING OF TIME API タイムアウト: resource={resource}"
            )

    async def write_record(self, resource: str, data: dict) -> dict:
        """KING OF TIME にレコードを書き込む（打刻修正等）。

        id キーがあれば PUT（更新）、なければ POST（新規登録）。

        Args:
            resource: リソース識別子
                "timerecords" — 打刻データ登録・修正
            data: 書き込むデータ。
                "id" キーがある場合は PUT（更新）として扱い、
                エンドポイントに /id を付与する。

        Returns:
            KING OF TIME API レスポンス dict

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
                    resp = await client.put(
                        f"{self.BASE_URL}/{resource}/{record_id}",
                        json=data,
                        headers=self.headers,
                    )
                else:
                    resp = await client.post(
                        f"{self.BASE_URL}/{resource}",
                        json=data,
                        headers=self.headers,
                    )
                self._handle_error_response(resp)
                return resp.json()
        except (PermissionError, RuntimeError):
            raise
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"KING OF TIME API タイムアウト: resource={resource}"
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
                    f"{self.BASE_URL}/employees",
                    headers=self.headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

    @staticmethod
    def map_to_attendance_data(kot_data: dict) -> dict:
        """KING OF TIME レスポンスを共通勤怠データ形式にマッピング。

        Args:
            kot_data: KING OF TIME API の日次勤怠オブジェクト

        Returns:
            共通勤怠データ形式:
            {
                "employee_id":       str  — 従業員コード
                "date":              str  — 対象日 (YYYY-MM-DD)
                "clock_in":          str  — 出勤時刻 (HH:MM)
                "clock_out":         str  — 退勤時刻 (HH:MM)
                "break_minutes":     int  — 休憩時間（分）
                "working_minutes":   int  — 実労働時間（分）
                "overtime_minutes":  int  — 残業時間（分）
                "late":              bool — 遅刻フラグ
                "early_leave":       bool — 早退フラグ
                "absence":           bool — 欠勤フラグ
            }
        """
        # 時刻は "HH:MM:SS" 形式で来る場合があるため先頭5文字だけ取る
        clock_in_raw = kot_data.get("start_time") or kot_data.get("clock_in", "")
        clock_out_raw = kot_data.get("end_time") or kot_data.get("clock_out", "")
        clock_in = clock_in_raw[:5] if clock_in_raw else ""
        clock_out = clock_out_raw[:5] if clock_out_raw else ""

        # 時間は分単位で返ってくることも、時間単位の場合もある
        # KING OF TIME API は分単位が多いが、フィールド名でも判断
        break_minutes: int = int(kot_data.get("break_minutes", 0) or 0)
        working_minutes: int = int(kot_data.get("work_minutes", 0) or 0)
        overtime_minutes: int = int(kot_data.get("overtime_minutes", 0) or 0)

        return {
            "employee_id": str(kot_data.get("employee_key", "")),
            "date": kot_data.get("date", ""),
            "clock_in": clock_in,
            "clock_out": clock_out,
            "break_minutes": break_minutes,
            "working_minutes": working_minutes,
            "overtime_minutes": overtime_minutes,
            "late": bool(kot_data.get("late", False)),
            "early_leave": bool(kot_data.get("early_leave", False)),
            "absence": bool(kot_data.get("absence", False)),
        }

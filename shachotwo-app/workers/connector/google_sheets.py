"""GoogleSheetsConnector — Google Sheets API コネクタ。読み書き統合。"""
import logging
import os
from typing import Any

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetsConnector(BaseConnector):
    """Google Sheets API コネクタ。

    credentials:
        credentials_path (str): サービスアカウントJSONファイルのパス
        spreadsheet_id (str): スプレッドシートID

    resource:
        シート名 + レンジ（例: "営業リスト!A2:L"）

    read_records の filters:
        columns (list[str], optional): 行をdictに変換する際のカラム名リスト

    write_record の data:
        range (str): 書き込み先レンジ（例: "営業リスト!D5:F5"）
        values (list[list[str]]): 書き込む2次元配列
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._service = None

    def _get_service(self):
        """Google Sheets API サービスオブジェクトを取得（遅延初期化）"""
        if self._service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            creds_path = self.config.credentials.get(
                "credentials_path",
                os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
            )
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    @property
    def spreadsheet_id(self) -> str:
        return self.config.credentials.get(
            "spreadsheet_id",
            os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
        )

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Google Sheets からレコードを読み取る。

        Args:
            resource: シート名+レンジ（例: "営業リスト!A2:L"）
            filters:
                columns (list[str]): カラム名リスト。指定時は各行をdictに変換

        Returns:
            list[dict] — columns 指定時はカラム名付きdictのリスト。
                         未指定時は {"row_index": i, "values": [...]} 形式
        """
        service = self._get_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=resource,
        ).execute()
        rows: list[list[str]] = result.get("values", [])

        columns = filters.get("columns")
        if columns:
            return [
                {
                    **{k: (row[i] if i < len(row) else "") for i, k in enumerate(columns)},
                    "row_number": idx + 2,
                }
                for idx, row in enumerate(rows)
            ]
        else:
            return [
                {"row_index": idx, "values": row}
                for idx, row in enumerate(rows)
            ]

    async def write_record(self, resource: str, data: dict) -> dict:
        """Google Sheets にデータを書き込む。

        Args:
            resource: 未使用（data 内の range を使用）
            data:
                range (str): 書き込み先レンジ（例: "営業リスト!D5:F5"）
                values (list[list[str]]): 書き込む2次元配列

        Returns:
            Sheets API のレスポンス dict
        """
        service = self._get_service()
        write_range = data.get("range", resource)
        values = data.get("values", [])

        result = service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=write_range,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        return result

    async def health_check(self) -> bool:
        """スプレッドシートのメタデータ取得で疎通確認。"""
        try:
            service = self._get_service()
            service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
            ).execute()
            return True
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # 便利メソッド（営業リスト操作ショートカット）
    # -----------------------------------------------------------------------

    async def get_all_rows(self, sheet_name: str = "営業リスト") -> list[dict]:
        """シート全行を営業リスト形式で取得"""
        columns = [
            "name", "industry", "hp_url", "sent", "sent_at", "method",
            "notes", "lp_url", "lp_viewed", "meeting_request", "contact_name", "phone",
        ]
        return await self.read_records(f"{sheet_name}!A2:L", {"columns": columns})

    async def get_unsent_companies(self, sheet_name: str = "営業リスト") -> list[dict]:
        """送信済フラグが空の行を取得"""
        rows = await self.get_all_rows(sheet_name)
        return [r for r in rows if not r.get("sent")]

    async def mark_sent(self, row: int, method: str, timestamp: str) -> dict:
        """送信成功マーク: D列, E列日時, F列送信方法"""
        return await self.write_record("", {
            "range": f"営業リスト!D{row}:F{row}",
            "values": [["OK", timestamp, method]],
        })

    async def mark_failed(self, row: int, reason: str) -> dict:
        """送信失敗マーク: G列"""
        return await self.write_record("", {
            "range": f"営業リスト!G{row}",
            "values": [[reason]],
        })

    async def update_lp_url(self, row: int, lp_url: str) -> dict:
        """LP URL記入: H列"""
        return await self.write_record("", {
            "range": f"営業リスト!H{row}",
            "values": [[lp_url]],
        })

    async def update_meeting_request(
        self, row: int, name: str, phone: str, timestamp: str
    ) -> dict:
        """面談希望記録: J列日時, K列担当者名, L列電話番号"""
        return await self.write_record("", {
            "range": f"営業リスト!J{row}:L{row}",
            "values": [[timestamp, name, phone]],
        })

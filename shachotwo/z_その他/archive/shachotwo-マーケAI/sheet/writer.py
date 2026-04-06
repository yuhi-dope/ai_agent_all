"""Google Sheets API — 結果書き戻し"""

from __future__ import annotations

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_service():
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _update_cells(range_str: str, values: list[list[str]]) -> None:
    service = _get_service()
    service.spreadsheets().values().update(
        spreadsheetId=settings.google_sheets_spreadsheet_id,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def mark_sent(row: int, method: str, timestamp: str) -> None:
    """送信成功: D列✅, E列日時, F列送信方法"""
    _update_cells(f"営業リスト!D{row}:F{row}", [["✅", timestamp, method]])


def mark_failed(row: int, reason: str) -> None:
    """送信失敗: G列に理由"""
    _update_cells(f"営業リスト!G{row}", [[reason]])


def update_lp_url(row: int, lp_url: str) -> None:
    """LP URL記入: H列"""
    _update_cells(f"営業リスト!H{row}", [[lp_url]])


def update_lp_view(row: int, timestamp: str) -> None:
    """LP閲覧記録: I列"""
    _update_cells(f"営業リスト!I{row}", [[timestamp]])


def update_meeting_request(row: int, name: str, phone: str, timestamp: str) -> None:
    """面談希望: J列日時, K列担当者名, L列電話番号"""
    _update_cells(f"営業リスト!J{row}:L{row}", [[timestamp, name, phone]])


def mark_error(row: int, error: str) -> None:
    """エラー: G列"""
    _update_cells(f"営業リスト!G{row}", [[f"❌ {error}"]])

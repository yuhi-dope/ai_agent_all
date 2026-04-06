"""Google Sheets API — 営業リスト読み取り"""

from __future__ import annotations

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
# カラム: A=企業名, B=業種, C=HP URL, D=送信済, E=送信日時, F=送信方法, G=備考,
#         H=LP URL, I=LP閲覧, J=面談希望, K=担当者名, L=電話番号


def _get_service():
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def get_all_rows(sheet_name: str = "営業リスト") -> list[dict]:
    """シート全行を取得"""
    service = _get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=settings.google_sheets_spreadsheet_id,
        range=f"{sheet_name}!A2:L",
    ).execute()
    rows = result.get("values", [])
    keys = ["name", "industry", "hp_url", "sent", "sent_at", "method", "notes", "lp_url", "lp_viewed", "meeting_request", "contact_name", "phone"]
    return [
        {**{k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)}, "row_number": idx + 2}
        for idx, row in enumerate(rows)
    ]


def get_unsent_companies() -> list[dict]:
    """D列（送信済）が空の行を取得"""
    return [r for r in get_all_rows() if not r.get("sent")]


def get_retry_companies() -> list[dict]:
    """G列に「⚠️」がある行を取得（デフォルト値追加後の再送信対象）"""
    return [r for r in get_all_rows() if "⚠️" in r.get("notes", "") and not r.get("sent")]

"""デフォルト値シート読み込み"""

from __future__ import annotations

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _get_service():
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def get_defaults() -> dict[str, str]:
    """「デフォルト値」シートから項目パターン→入力値のマッピングを取得"""
    service = _get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=settings.google_sheets_spreadsheet_id,
        range="デフォルト値!A2:B",
    ).execute()
    rows = result.get("values", [])
    return {row[0]: row[1] for row in rows if len(row) >= 2}


def match_default(field_label: str, defaults: dict[str, str]) -> str | None:
    """フォーム項目名とデフォルト値のファジーマッチング"""
    field_lower = field_label.lower().strip()
    for pattern, value in defaults.items():
        if pattern.lower() in field_lower or field_lower in pattern.lower():
            return value
    return None

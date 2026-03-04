"""Google Workspace Typed Function Tools."""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Any

from server.saas.tools.http import SaaSCreds, api_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

register_saas(SaaSMetadata(
    saas_name="google_workspace",
    display_name="Google Workspace",
    genre="productivity",
    description="Gmail送受信・Googleカレンダー管理・Googleドライブ操作・スプレッドシート編集",
    supported_auth_methods=["oauth2"],
    default_scopes=[
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ],
))


# --- Gmail ---

@saas_tool(saas="google_workspace", genre="productivity")
async def gmail_send(to: str, subject: str, body: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """Gmail でメールを送信する"""
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return await api_request(
        "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        creds=creds, json={"raw": raw},
    )


@saas_tool(saas="google_workspace", genre="productivity")
async def gmail_search(query: str, max_results: int = 10, *, creds: SaaSCreds) -> dict[str, Any]:
    """Gmail でメールを検索する"""
    params: dict[str, Any] = {"q": query, "maxResults": max_results}
    return await api_request(
        "GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        creds=creds, params=params,
    )


@saas_tool(saas="google_workspace", genre="productivity")
async def gmail_read(message_id: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """Gmail のメール内容を読み取る"""
    return await api_request(
        "GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        creds=creds, params={"format": "full"},
    )


# --- Google Calendar ---

@saas_tool(saas="google_workspace", genre="productivity")
async def gcal_list_events(
    time_min: str = "", time_max: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Google Calendar の予定一覧を取得する"""
    params: dict[str, Any] = {}
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    return await api_request(
        "GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        creds=creds, params=params,
    )


@saas_tool(saas="google_workspace", genre="productivity")
async def gcal_create_event(
    summary: str, start: str, end: str, attendees: list | None = None, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Google Calendar に予定を作成する"""
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    return await api_request(
        "POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        creds=creds, json=body,
    )


# --- Google Drive ---

@saas_tool(saas="google_workspace", genre="productivity")
async def gdrive_list_files(
    query: str = "", folder_id: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Google Drive のファイル一覧を取得する"""
    params: dict[str, Any] = {"fields": "files(id,name,mimeType,modifiedTime)"}
    if query:
        params["q"] = query
    return await api_request(
        "GET", "https://www.googleapis.com/drive/v3/files",
        creds=creds, params=params,
    )


@saas_tool(saas="google_workspace", genre="productivity")
async def gdrive_read_file(file_id: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """Google Drive のファイル内容を読み取る"""
    return await api_request(
        "GET", f"https://www.googleapis.com/drive/v3/files/{file_id}",
        creds=creds, params={"alt": "media"},
    )


# --- Google Sheets ---

@saas_tool(saas="google_workspace", genre="productivity")
async def gsheets_read(spreadsheet_id: str, range: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """Google Sheets のデータを読み取る"""
    return await api_request(
        "GET", f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range}",
        creds=creds,
    )


@saas_tool(saas="google_workspace", genre="productivity")
async def gsheets_write(
    spreadsheet_id: str, range: str, values: list, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Google Sheets にデータを書き込む"""
    return await api_request(
        "PUT",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range}",
        creds=creds, params={"valueInputOption": "USER_ENTERED"},
        json={"values": values},
    )

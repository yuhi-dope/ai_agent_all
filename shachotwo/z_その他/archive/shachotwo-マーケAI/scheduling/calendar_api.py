"""Google Calendar API — 空き枠取得 & 予定作成"""

from __future__ import annotations

from datetime import datetime, timedelta
import uuid

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from pydantic import BaseModel

from config import settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class TimeSlot(BaseModel):
    id: str = ""
    start: datetime
    end: datetime
    date_label: str = ""  # "3/18（火）"
    time_label: str = ""  # "10:00〜10:30"


class MeetingInfo(BaseModel):
    calendar_event_id: str
    meet_url: str
    start: datetime
    end: datetime
    title: str


def _get_service():
    creds = Credentials.from_service_account_file(settings.google_credentials_path, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _is_business_day(dt: datetime) -> bool:
    return dt.weekday() < 5  # 月〜金


def get_available_slots(days_ahead: int = 5) -> list[TimeSlot]:
    """翌営業日〜5営業日後の空き30分枠を取得"""
    service = _get_service()
    now = datetime.now()
    start_date = now + timedelta(days=1)

    # 営業日を収集
    business_days = []
    d = start_date
    while len(business_days) < days_ahead:
        if _is_business_day(d):
            business_days.append(d.date())
        d += timedelta(days=1)

    time_min = datetime.combine(business_days[0], datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(business_days[-1], datetime.max.time()).isoformat() + "Z"

    events = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute().get("items", [])

    busy = set()
    for ev in events:
        s = ev.get("start", {}).get("dateTime", "")
        if s:
            busy.add(s[:16])  # "2026-03-18T10:00"

    slots = []
    weekday_ja = ["月", "火", "水", "木", "金", "土", "日"]
    for day in business_days:
        for hour in range(9, 18):
            for minute in (0, 30):
                start = datetime(day.year, day.month, day.day, hour, minute)
                end = start + timedelta(minutes=30)
                if start.isoformat()[:16] not in busy:
                    slots.append(TimeSlot(
                        id=str(uuid.uuid4())[:8],
                        start=start,
                        end=end,
                        date_label=f"{start.month}/{start.day}（{weekday_ja[start.weekday()]}）",
                        time_label=f"{start.strftime('%H:%M')}〜{end.strftime('%H:%M')}",
                    ))

    return slots


def create_meeting(slot: TimeSlot, company_name: str, contact_name: str, attendee_email: str = "") -> MeetingInfo:
    """Google Calendar予定+Meet作成"""
    service = _get_service()
    title = f"シャチョツー ご紹介 — {company_name} {contact_name}様"

    event_body = {
        "summary": title,
        "start": {"dateTime": slot.start.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": slot.end.isoformat(), "timeZone": "Asia/Tokyo"},
        "conferenceData": {
            "createRequest": {"requestId": str(uuid.uuid4()), "conferenceSolutionKey": {"type": "hangoutsMeet"}},
        },
    }
    if attendee_email:
        event_body["attendees"] = [{"email": attendee_email}, {"email": settings.sender_email}]

    event = service.events().insert(
        calendarId="primary", body=event_body, conferenceDataVersion=1
    ).execute()

    meet_url = event.get("conferenceData", {}).get("entryPoints", [{}])[0].get("uri", "")
    return MeetingInfo(
        calendar_event_id=event["id"],
        meet_url=meet_url,
        start=slot.start,
        end=slot.end,
        title=title,
    )

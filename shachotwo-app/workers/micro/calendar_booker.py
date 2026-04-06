"""calendar_booker マイクロエージェント。Google Calendar 空き枠取得 + Meet付き予約作成。"""
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------

class TimeSlot(BaseModel):
    """空き時間枠"""
    id: str = ""
    start: datetime
    end: datetime
    date_label: str = ""  # "3/18（火）"
    time_label: str = ""  # "10:00〜10:30"


class MeetingInfo(BaseModel):
    """作成された会議情報"""
    calendar_event_id: str
    meet_url: str
    start: datetime
    end: datetime
    title: str


# ---------------------------------------------------------------------------
# Google Calendar API ヘルパー
# ---------------------------------------------------------------------------

def _get_service():
    """Google Calendar API サービスオブジェクトを取得。

    環境変数 GOOGLE_CREDENTIALS_PATH からサービスアカウント認証情報を読む。
    """
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)


def _is_business_day(dt: datetime) -> bool:
    return dt.weekday() < 5  # 月〜金


async def _get_available_slots(days_ahead: int = 5) -> list[TimeSlot]:
    """翌営業日から指定営業日数分の空き30分枠を取得"""
    service = _get_service()
    now = datetime.now()
    start_date = now + timedelta(days=1)

    # 営業日を収集
    business_days: list = []
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

    busy: set[str] = set()
    for ev in events:
        s = ev.get("start", {}).get("dateTime", "")
        if s:
            busy.add(s[:16])

    slots: list[TimeSlot] = []
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
                        date_label=f"{start.month}/{start.day}（{WEEKDAY_JA[start.weekday()]}）",
                        time_label=f"{start.strftime('%H:%M')}〜{end.strftime('%H:%M')}",
                    ))

    return slots


async def _create_meeting(
    slot: TimeSlot,
    company_name: str,
    contact_name: str,
    attendee_email: str = "",
) -> MeetingInfo:
    """Google Calendar 予定 + Meet リンクを作成"""
    service = _get_service()
    sender_email = os.environ.get("SENDER_EMAIL", "")
    title = f"シャチョツー ご紹介 — {company_name} {contact_name}様"

    event_body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": slot.start.isoformat(), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": slot.end.isoformat(), "timeZone": "Asia/Tokyo"},
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            },
        },
    }
    if attendee_email:
        attendees = [{"email": attendee_email}]
        if sender_email:
            attendees.append({"email": sender_email})
        event_body["attendees"] = attendees

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


# ---------------------------------------------------------------------------
# マイクロエージェント run 関数
# ---------------------------------------------------------------------------

async def run_calendar_booker(input: MicroAgentInput) -> MicroAgentOutput:
    """
    Google Calendar 空き枠取得 or Meet 付き予約作成を行う。

    payload:
        action (str): "get_slots" | "create_meeting"

        # get_slots の場合:
        days_ahead (int, optional): 何営業日先まで取得するか（デフォルト5）

        # create_meeting の場合:
        slot (dict): TimeSlot のデータ（start, end 必須）
        company_name (str): 企業名
        contact_name (str): 担当者名
        attendee_email (str, optional): 参加者メール

    result:
        # get_slots: slots (list[dict])
        # create_meeting: meeting (dict)
    """
    start_ms = int(time.time() * 1000)
    agent_name = "calendar_booker"

    try:
        action = input.payload.get("action", "get_slots")

        if action == "get_slots":
            days_ahead = input.payload.get("days_ahead", 5)
            slots = await _get_available_slots(days_ahead)
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name,
                success=True,
                result={"slots": [s.model_dump(mode="json") for s in slots]},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        elif action == "create_meeting":
            slot_data = input.payload.get("slot")
            if not slot_data:
                raise MicroAgentError(agent_name, "input_validation", "slot が必要です")

            slot = TimeSlot(**slot_data)
            company_name = input.payload.get("company_name", "")
            contact_name = input.payload.get("contact_name", "")
            attendee_email = input.payload.get("attendee_email", "")

            if not company_name:
                raise MicroAgentError(agent_name, "input_validation", "company_name が必要です")

            meeting = await _create_meeting(slot, company_name, contact_name, attendee_email)
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name,
                success=True,
                result={"meeting": meeting.model_dump(mode="json")},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        else:
            raise MicroAgentError(agent_name, "input_validation", f"不明なaction: {action}")

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"calendar_booker error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

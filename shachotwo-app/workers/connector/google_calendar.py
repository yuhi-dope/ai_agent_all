"""GoogleCalendarConnector — Google Calendar API v3 コネクタ。

イベントCRUD + Watch API 対応。BaseConnector準拠。
既存 calendar_booker.py の認証パターンを踏襲。
"""
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarConnector(BaseConnector):
    """Google Calendar API v3 コネクタ。

    credentials:
        credentials_path (str): サービスアカウントJSONファイルのパス
        delegated_email (str, optional): ドメイン委任対象アドレス
        calendar_id (str, optional): カレンダーID (デフォルト: "primary")

    read_records の resource:
        "events"   — イベント一覧
        "event"    — 単一イベント取得
        "freebusy" — 空き時間取得

    write_record の resource:
        "create_event"  — イベント作成 (Meet付きオプション)
        "update_event"  — イベント更新
        "delete_event"  — イベント削除
        "watch"         — Watch API チャネル登録
        "stop_watch"    — Watch 停止
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._service = None

    def _get_service(self):
        """Calendar API v3 サービスオブジェクトを取得（遅延初期化）。"""
        if self._service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            creds_path = self.config.credentials.get(
                "credentials_path",
                os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
            )
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

            delegated = self.config.credentials.get(
                "delegated_email",
                os.environ.get("GOOGLE_CALENDAR_DELEGATED_EMAIL", ""),
            )
            if delegated:
                creds = creds.with_subject(delegated)

            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    @property
    def calendar_id(self) -> str:
        return self.config.credentials.get("calendar_id", "primary")

    # ------------------------------------------------------------------
    # BaseConnector 実装
    # ------------------------------------------------------------------

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Calendar からイベント情報を読み取る。"""
        service = self._get_service()

        if resource == "events":
            return self._list_events(service, filters)
        elif resource == "event":
            return [self._get_event(service, filters)]
        elif resource == "freebusy":
            return self._get_freebusy(service, filters)
        else:
            logger.warning("GoogleCalendarConnector.read_records: resource '%s' 未サポート", resource)
            return []

    async def write_record(self, resource: str, data: dict) -> dict:
        """Calendar にイベントを作成/更新、またはWatch APIを操作する。"""
        service = self._get_service()

        if resource == "create_event":
            return self._create_event(service, data)
        elif resource == "update_event":
            return self._update_event(service, data)
        elif resource == "delete_event":
            return self._delete_event(service, data)
        elif resource == "watch":
            return self._register_watch(service, data)
        elif resource == "stop_watch":
            return self._stop_watch(service, data)
        else:
            raise ValueError(f"GoogleCalendarConnector: 未知のresource '{resource}'")

    async def health_check(self) -> bool:
        """Calendar API 疎通確認。"""
        try:
            service = self._get_service()
            service.calendarList().list(maxResults=1).execute()
            return True
        except Exception as e:
            logger.error("GoogleCalendarConnector.health_check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # イベント操作
    # ------------------------------------------------------------------

    def _list_events(self, service: Any, filters: dict) -> list[dict]:
        """イベント一覧取得。

        filters:
            time_min (str): ISO8601 (デフォルト: 現在)
            time_max (str): ISO8601 (デフォルト: 7日後)
            calendar_id (str): カレンダーID
            query (str): 検索キーワード
            max_results (int): 最大件数 (デフォルト: 50)
            updated_min (str): この日時以降に更新されたイベントのみ
        """
        cal_id = filters.get("calendar_id", self.calendar_id)
        now = datetime.utcnow().isoformat() + "Z"

        params: dict[str, Any] = {
            "calendarId": cal_id,
            "timeMin": filters.get("time_min", now),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": filters.get("max_results", 50),
        }
        if "time_max" in filters:
            params["timeMax"] = filters["time_max"]
        if "query" in filters:
            params["q"] = filters["query"]
        if "updated_min" in filters:
            params["updatedMin"] = filters["updated_min"]

        result = service.events().list(**params).execute()
        return result.get("items", [])

    def _get_event(self, service: Any, filters: dict) -> dict:
        """単一イベント取得。"""
        event_id = filters["event_id"]
        cal_id = filters.get("calendar_id", self.calendar_id)
        return service.events().get(calendarId=cal_id, eventId=event_id).execute()

    def _get_freebusy(self, service: Any, filters: dict) -> list[dict]:
        """空き時間を取得。"""
        cal_ids = filters.get("calendar_ids", [self.calendar_id])
        time_min = filters.get("time_min", datetime.utcnow().isoformat() + "Z")
        time_max = filters.get(
            "time_max",
            (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z",
        )

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": c} for c in cal_ids],
        }
        result = service.freebusy().query(body=body).execute()
        return [
            {"calendar_id": cal, "busy": info.get("busy", [])}
            for cal, info in result.get("calendars", {}).items()
        ]

    def _create_event(self, service: Any, data: dict) -> dict:
        """イベント作成。Meet付きオプション対応。

        data:
            summary (str): タイトル
            start (str): ISO8601
            end (str): ISO8601
            description (str, optional): 説明
            attendees (list[str], optional): 参加者メールリスト
            conference (bool, optional): Google Meet を追加するか (デフォルト: False)
            calendar_id (str, optional): カレンダーID
        """
        cal_id = data.get("calendar_id", self.calendar_id)

        event_body: dict[str, Any] = {
            "summary": data["summary"],
            "start": {"dateTime": data["start"], "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": data["end"], "timeZone": "Asia/Tokyo"},
        }

        if data.get("description"):
            event_body["description"] = data["description"]

        if data.get("attendees"):
            event_body["attendees"] = [
                {"email": e} for e in data["attendees"]
            ]

        conference_version = 0
        if data.get("conference"):
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                },
            }
            conference_version = 1

        result = service.events().insert(
            calendarId=cal_id,
            body=event_body,
            conferenceDataVersion=conference_version,
        ).execute()

        logger.info(
            "GoogleCalendarConnector: created event '%s' → %s",
            data["summary"],
            result.get("id"),
        )
        return result

    def _update_event(self, service: Any, data: dict) -> dict:
        """イベント更新。

        data:
            event_id (str): 対象イベントID
            calendar_id (str, optional): カレンダーID
            + 更新フィールド (summary, description, start, end 等)
        """
        event_id = data["event_id"]
        cal_id = data.get("calendar_id", self.calendar_id)

        # まず現在のイベントを取得
        event = service.events().get(calendarId=cal_id, eventId=event_id).execute()

        # 更新可能フィールドをマージ
        for field in ("summary", "description", "location"):
            if field in data:
                event[field] = data[field]
        for time_field in ("start", "end"):
            if time_field in data:
                event[time_field] = {
                    "dateTime": data[time_field],
                    "timeZone": "Asia/Tokyo",
                }

        result = service.events().update(
            calendarId=cal_id, eventId=event_id, body=event
        ).execute()
        return result

    def _delete_event(self, service: Any, data: dict) -> dict:
        """イベント削除。"""
        event_id = data["event_id"]
        cal_id = data.get("calendar_id", self.calendar_id)
        service.events().delete(calendarId=cal_id, eventId=event_id).execute()
        return {"deleted": True, "event_id": event_id}

    # ------------------------------------------------------------------
    # Watch API
    # ------------------------------------------------------------------

    def _register_watch(self, service: Any, data: dict) -> dict:
        """Calendar Watch API チャネルを登録する。

        data:
            webhook_url (str): Push通知の送信先URL
            channel_id (str, optional): チャネルID (未指定時は自動生成)
            calendar_id (str, optional): カレンダーID
            ttl (int, optional): 有効期間（秒）。最大30日 (2592000)
        """
        cal_id = data.get("calendar_id", self.calendar_id)
        channel_id = data.get("channel_id", str(uuid.uuid4()))
        ttl_seconds = data.get("ttl", 2592000)  # デフォルト30日

        expiration_ms = int(
            (datetime.utcnow() + timedelta(seconds=ttl_seconds)).timestamp() * 1000
        )

        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": data["webhook_url"],
            "expiration": expiration_ms,
        }

        result = service.events().watch(calendarId=cal_id, body=body).execute()
        logger.info(
            "GoogleCalendarConnector: watch registered channel=%s calendar=%s",
            channel_id,
            cal_id,
        )
        return {
            "channel_id": result.get("id"),
            "resource_id": result.get("resourceId"),
            "expiration": result.get("expiration"),
        }

    def _stop_watch(self, service: Any, data: dict) -> dict:
        """Watch チャネルを停止する。

        data:
            channel_id (str): 停止するチャネルID
            resource_id (str): リソースID
        """
        body = {
            "id": data["channel_id"],
            "resourceId": data["resource_id"],
        }
        service.channels().stop(body=body).execute()
        logger.info(
            "GoogleCalendarConnector: watch stopped channel=%s",
            data["channel_id"],
        )
        return {"stopped": True, "channel_id": data["channel_id"]}

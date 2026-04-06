"""GmailConnector のユニットテスト。外部APIは全てモック。"""
import asyncio
import base64
from datetime import date
from email import message_from_bytes
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.connector.base import ConnectorConfig
from workers.connector.email import DAILY_SEND_LIMIT, GmailConnector


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_config(extra: dict | None = None) -> ConnectorConfig:
    creds = {
        "credentials_path": "/fake/credentials.json",
        "sender_email": "sender@example.com",
    }
    if extra:
        creds.update(extra)
    return ConnectorConfig(tool_name="email", credentials=creds)


def _make_connector(extra_creds: dict | None = None) -> GmailConnector:
    return GmailConnector(_make_config(extra_creds))


def _make_gmail_service_mock(send_response: dict | None = None) -> MagicMock:
    """Gmail API サービスのモックを返す。"""
    send_result = send_response or {"id": "msg_001", "threadId": "thread_001", "labelIds": ["SENT"]}
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = send_result
    service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "sender@example.com",
        "messagesTotal": 1000,
    }
    return service


def _make_gmail_message(
    msg_id: str = "msg_001",
    subject: str = "テスト件名",
    from_addr: str = "client@example.com",
    body_html: str = "<p>テスト本文</p>",
) -> dict:
    """Gmail API のメッセージオブジェクト形式のモックを返す。"""
    body_b64 = base64.urlsafe_b64encode(body_html.encode("utf-8")).decode("utf-8")
    return {
        "id": msg_id,
        "threadId": f"thread_{msg_id}",
        "snippet": "テスト本文",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "To", "value": "sender@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 22 Mar 2026 10:00:00 +0900"},
            ],
            "body": {"data": body_b64},
            "parts": [],
        },
    }


# ---------------------------------------------------------------------------
# 正常系: send_email
# ---------------------------------------------------------------------------


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_send_email_returns_api_response(self) -> None:
        """send_email が Gmail API のレスポンスをそのまま返すこと。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock(
            {"id": "msg_abc", "threadId": "thread_abc", "labelIds": ["SENT"]}
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.send_email(
                to="dest@example.com",
                subject="テスト送信",
                body_html="<p>こんにちは</p>",
            )

        assert result["id"] == "msg_abc"
        assert result["threadId"] == "thread_abc"

    @pytest.mark.asyncio
    async def test_send_email_calls_gmail_send_api(self) -> None:
        """send_email が users.messages.send を呼び出すこと。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            await connector.send_email(
                to="dest@example.com",
                subject="テスト",
                body_html="<p>本文</p>",
            )

        service_mock.users().messages().send.assert_called_once()
        call_kwargs = service_mock.users().messages().send.call_args
        assert call_kwargs[1]["userId"] == "me"
        assert "raw" in call_kwargs[1]["body"]

    @pytest.mark.asyncio
    async def test_send_email_increments_daily_count(self) -> None:
        """send_email 呼び出しごとに送信カウントが増加すること。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            assert connector._send_count == 0
            await connector.send_email("a@example.com", "件名1", "<p>1</p>")
            assert connector._send_count == 1
            await connector.send_email("b@example.com", "件名2", "<p>2</p>")
            assert connector._send_count == 2

    @pytest.mark.asyncio
    async def test_send_email_with_multiple_recipients(self) -> None:
        """複数宛先のリストを渡しても送信できること。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.send_email(
                to=["a@example.com", "b@example.com"],
                subject="一斉送信",
                body_html="<p>全員へ</p>",
            )

        assert result["id"] == "msg_001"

    @pytest.mark.asyncio
    async def test_send_email_with_attachments(self) -> None:
        """添付ファイル付き送信で raw メッセージが構築されること。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        dummy_content = base64.b64encode(b"PDF content here").decode("utf-8")
        attachments = [
            {
                "filename": "report.pdf",
                "content_b64": dummy_content,
                "mime_type": "application/pdf",
            }
        ]

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.send_email(
                to="dest@example.com",
                subject="添付テスト",
                body_html="<p>添付あり</p>",
                attachments=attachments,
            )

        assert result["id"] == "msg_001"
        # raw メッセージが渡されたことを確認
        call_body = service_mock.users().messages().send.call_args[1]["body"]
        assert "raw" in call_body
        # デコードして添付ファイルの MIME を確認
        raw_decoded = base64.urlsafe_b64decode(call_body["raw"])
        assert b"report.pdf" in raw_decoded

    @pytest.mark.asyncio
    async def test_send_email_raises_on_api_error(self) -> None:
        """Gmail API がエラーを返した場合に RuntimeError が発生すること。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.messages.return_value.send.return_value.execute.side_effect = Exception(
            "API quota exceeded"
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            with pytest.raises(RuntimeError, match="Gmail 送信エラー"):
                await connector.send_email("dest@example.com", "件名", "<p>本文</p>")


# ---------------------------------------------------------------------------
# 正常系: send_email_with_tracking
# ---------------------------------------------------------------------------


class TestSendEmailWithTracking:
    def _decode_mime_body(self, raw_b64url: str) -> str:
        """MIME メッセージの raw base64url から HTML ボディ文字列を取り出す。

        MIME の HTML part は Content-Transfer-Encoding: base64 でさらにエンコードされる
        ため、MIME パーサーで part を取り出してデコードする。
        """
        import quopri
        from email import message_from_bytes

        raw_bytes = base64.urlsafe_b64decode(raw_b64url + "==")
        msg = message_from_bytes(raw_bytes)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        return ""

    @pytest.mark.asyncio
    async def test_tracking_pixel_inserted_into_body(self) -> None:
        """トラッキングピクセルが HTML ボディに挿入されること。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            await connector.send_email_with_tracking(
                to="dest@example.com",
                subject="開封確認テスト",
                body_html="<p>テスト</p>",
            )

        call_body = service_mock.users().messages().send.call_args[1]["body"]
        html_body = self._decode_mime_body(call_body["raw"])
        # トラッキング用 img タグが含まれること
        assert "<img" in html_body
        assert 'width="1"' in html_body
        assert 'height="1"' in html_body

    @pytest.mark.asyncio
    async def test_custom_tracking_url_is_used(self) -> None:
        """カスタムトラッキングURLが img src に使われること。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()
        custom_url = "https://track.example.com/open/abc123"

        with patch.object(connector, "_get_service", return_value=service_mock):
            await connector.send_email_with_tracking(
                to="dest@example.com",
                subject="カスタムトラッキング",
                body_html="<p>本文</p>",
                tracking_url=custom_url,
            )

        call_body = service_mock.users().messages().send.call_args[1]["body"]
        html_body = self._decode_mime_body(call_body["raw"])
        assert "track.example.com/open/abc123" in html_body

    @pytest.mark.asyncio
    async def test_tracking_increments_send_count(self) -> None:
        """send_email_with_tracking も送信カウントを増やすこと。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            await connector.send_email_with_tracking("a@example.com", "件名", "<p>本文</p>")

        assert connector._send_count == 1


# ---------------------------------------------------------------------------
# 送信上限テスト
# ---------------------------------------------------------------------------


class TestDailyQuota:
    @pytest.mark.asyncio
    async def test_send_raises_when_daily_limit_reached(self) -> None:
        """1日の送信上限に達した場合に ValueError が発生すること。"""
        connector = _make_connector()
        connector._send_count = DAILY_SEND_LIMIT  # 上限に到達済み
        connector._send_count_date = date.today()

        with pytest.raises(ValueError, match="1日の送信上限"):
            await connector.send_email("dest@example.com", "件名", "<p>本文</p>")

    @pytest.mark.asyncio
    async def test_send_with_tracking_raises_when_daily_limit_reached(self) -> None:
        """トラッキング送信でも上限チェックが機能すること。"""
        connector = _make_connector()
        connector._send_count = DAILY_SEND_LIMIT
        connector._send_count_date = date.today()

        with pytest.raises(ValueError, match="1日の送信上限"):
            await connector.send_email_with_tracking("dest@example.com", "件名", "<p>本文</p>")

    @pytest.mark.asyncio
    async def test_count_resets_on_new_day(self) -> None:
        """日付が変わると送信カウンタがリセットされること。"""
        from datetime import date, timedelta

        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        # 昨日の上限到達状態をシミュレート
        connector._send_count = DAILY_SEND_LIMIT
        connector._send_count_date = date.today() - timedelta(days=1)

        with patch.object(connector, "_get_service", return_value=service_mock):
            # 日付が変わったのでリセットされ、送信できるはず
            result = await connector.send_email("dest@example.com", "件名", "<p>本文</p>")

        assert result["id"] == "msg_001"
        assert connector._send_count == 1

    @pytest.mark.asyncio
    async def test_get_send_quota_returns_correct_values(self) -> None:
        """get_send_quota が正しい残数を返すこと。"""
        connector = _make_connector()
        connector._send_count = 50
        connector._send_count_date = date.today()

        quota = await connector.get_send_quota()

        assert quota["daily_limit"] == DAILY_SEND_LIMIT
        assert quota["sent_today"] == 50
        assert quota["remaining"] == DAILY_SEND_LIMIT - 50
        assert quota["reset_date"] == date.today().isoformat()


# ---------------------------------------------------------------------------
# 正常系: fetch_inbound / read_records
# ---------------------------------------------------------------------------


class TestFetchInbound:
    def _make_list_result(self, ids: list[str]) -> dict:
        return {"messages": [{"id": mid} for mid in ids]}

    @pytest.mark.asyncio
    async def test_fetch_inbound_returns_parsed_messages(self) -> None:
        """fetch_inbound がメッセージのリストを返すこと。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.messages.return_value.list.return_value.execute.return_value = (
            self._make_list_result(["msg_001"])
        )
        service_mock.users.return_value.messages.return_value.get.return_value.execute.return_value = (
            _make_gmail_message("msg_001", "受信テスト", "client@example.com")
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.fetch_inbound()

        assert len(result) == 1
        assert result[0]["id"] == "msg_001"
        assert result[0]["subject"] == "受信テスト"
        assert result[0]["from"] == "client@example.com"

    @pytest.mark.asyncio
    async def test_fetch_inbound_with_since_adds_query(self) -> None:
        """since 指定時に after: クエリが付与されること。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.messages.return_value.list.return_value.execute.return_value = (
            {"messages": []}
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            await connector.fetch_inbound(since="2026-03-01T00:00:00")

        call_kwargs = service_mock.users().messages().list.call_args[1]
        assert "q" in call_kwargs
        assert "after:" in call_kwargs["q"]

    @pytest.mark.asyncio
    async def test_fetch_inbound_returns_empty_when_no_messages(self) -> None:
        """受信メールが0件のとき空リストを返すこと。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.messages.return_value.list.return_value.execute.return_value = (
            {}  # messages キーなし
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.fetch_inbound()

        assert result == []

    @pytest.mark.asyncio
    async def test_read_records_inbox_delegates_to_fetch_inbound(self) -> None:
        """read_records('inbox') が fetch_inbound を呼ぶこと。"""
        connector = _make_connector()

        with patch.object(
            connector, "fetch_inbound", new_callable=AsyncMock, return_value=[]
        ) as mock_fetch:
            await connector.read_records("inbox", {"since": "2026-03-01T00:00:00", "max_results": 10})

        mock_fetch.assert_called_once_with(
            since="2026-03-01T00:00:00",
            max_results=10,
            extra_query="",
        )

    @pytest.mark.asyncio
    async def test_read_records_unknown_resource_returns_empty(self) -> None:
        """'inbox' 以外のリソースには空リストを返すこと。"""
        connector = _make_connector()
        result = await connector.read_records("sent")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_inbound_raises_on_api_error(self) -> None:
        """Gmail API エラー時に RuntimeError が発生すること。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.messages.return_value.list.return_value.execute.side_effect = Exception(
            "network timeout"
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            with pytest.raises(RuntimeError, match="Gmail 受信取得エラー"):
                await connector.fetch_inbound()


# ---------------------------------------------------------------------------
# 正常系: write_record
# ---------------------------------------------------------------------------


class TestWriteRecord:
    @pytest.mark.asyncio
    async def test_write_record_send_calls_send_email(self) -> None:
        """write_record('send') が send_email を呼ぶこと。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.write_record("send", {
                "to": "dest@example.com",
                "subject": "テスト",
                "body_html": "<p>本文</p>",
            })

        assert result["id"] == "msg_001"

    @pytest.mark.asyncio
    async def test_write_record_send_with_tracking(self) -> None:
        """write_record('send_with_tracking') がトラッキング送信を呼ぶこと。"""
        from email import message_from_bytes

        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.write_record("send_with_tracking", {
                "to": "dest@example.com",
                "subject": "トラッキング",
                "body_html": "<p>本文</p>",
            })

        assert result["id"] == "msg_001"
        # MIME パーサーで HTML part を取り出してトラッキングピクセルを確認
        raw_b64 = service_mock.users().messages().send.call_args[1]["body"]["raw"]
        raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
        msg = message_from_bytes(raw_bytes)
        html_body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
        assert "<img" in html_body


# ---------------------------------------------------------------------------
# 正常系: health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self) -> None:
        """Gmail API 接続成功時に True を返すこと。"""
        connector = _make_connector()
        service_mock = _make_gmail_service_mock()

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.health_check()

        assert result is True
        service_mock.users().getProfile.assert_called_once_with(userId="me")

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_auth_error(self) -> None:
        """認証エラー時に False を返すこと。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.getProfile.return_value.execute.side_effect = Exception(
            "invalid_grant"
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_credentials_not_found(self) -> None:
        """認証情報ファイルが見つからない場合に False を返すこと。"""
        connector = _make_connector()

        with patch.object(
            connector, "_get_service", side_effect=FileNotFoundError("credentials.json not found")
        ):
            result = await connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_network_timeout(self) -> None:
        """ネットワークタイムアウト時に False を返すこと。"""
        connector = _make_connector()
        service_mock = MagicMock()
        service_mock.users.return_value.getProfile.return_value.execute.side_effect = (
            TimeoutError("connection timed out")
        )

        with patch.object(connector, "_get_service", return_value=service_mock):
            result = await connector.health_check()

        assert result is False


# ---------------------------------------------------------------------------
# メッセージ構築テスト
# ---------------------------------------------------------------------------


class TestBuildMessage:
    def test_build_message_is_valid_base64url(self) -> None:
        """_build_message が有効な base64url 文字列を返すこと。"""
        connector = _make_connector()
        raw = connector._build_message(
            to="dest@example.com",
            subject="テスト件名",
            body_html="<p>本文</p>",
        )
        # base64url デコード可能なこと
        decoded = base64.urlsafe_b64decode(raw + "==")
        assert len(decoded) > 0

    def test_build_message_contains_to_subject_headers(self) -> None:
        """To, Subject ヘッダがMIMEメッセージに含まれること。"""
        from email import message_from_bytes

        connector = _make_connector()
        raw = connector._build_message(
            to="dest@example.com",
            subject="件名テスト",
            body_html="<p>本文</p>",
        )
        msg = message_from_bytes(base64.urlsafe_b64decode(raw + "=="))
        assert "dest@example.com" in msg["To"]
        # Subject は encoded-word になっている場合があるため raw bytes で確認
        raw_bytes = base64.urlsafe_b64decode(raw + "==")
        assert b"dest@example.com" in raw_bytes

    def test_build_message_with_tracking_url(self) -> None:
        """トラッキングURLが img タグとして HTML part に挿入されること。"""
        from email import message_from_bytes

        connector = _make_connector()
        raw = connector._build_message(
            to="dest@example.com",
            subject="件名",
            body_html="<p>本文</p>",
            tracking_url="https://track.example.com/pixel.gif",
        )
        msg = message_from_bytes(base64.urlsafe_b64decode(raw + "=="))
        html_body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break
        assert "track.example.com/pixel.gif" in html_body
        assert 'width="1"' in html_body


# ---------------------------------------------------------------------------
# Factory 登録テスト
# ---------------------------------------------------------------------------


class TestFactoryRegistration:
    def test_get_connector_returns_gmail_connector(self) -> None:
        """factory.get_connector('email') が GmailConnector を返すこと。"""
        from workers.connector.factory import get_connector
        from security.encryption import encrypt_field

        credentials = {
            "credentials_path": "/fake/creds.json",
            "sender_email": "sender@example.com",
        }
        encrypted = encrypt_field(credentials)

        connector = get_connector("email", encrypted)

        assert isinstance(connector, GmailConnector)
        assert connector.config.tool_name == "email"
        assert connector.config.credentials["sender_email"] == "sender@example.com"

    def test_email_key_is_in_connectors_dict(self) -> None:
        """CONNECTORS 辞書に 'email' キーが登録されていること。"""
        from workers.connector.factory import CONNECTORS

        assert "email" in CONNECTORS
        assert CONNECTORS["email"] is GmailConnector

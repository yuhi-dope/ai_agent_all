"""notifier.py のユニットテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# ヘルパー: モック ConnectorConfig / GmailConnector / SlackConnector
# ---------------------------------------------------------------------------

def _make_gmail_mock() -> AsyncMock:
    """GmailConnector の write_record を成功させるモック。"""
    mock = AsyncMock()
    mock.write_record = AsyncMock(return_value={"id": "msg_abc123"})
    return mock


def _make_slack_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.write_record = AsyncMock(return_value={"ok": True})
    return mock


# ---------------------------------------------------------------------------
# _build_subject
# ---------------------------------------------------------------------------

class TestBuildSubject:
    def test_completed(self):
        from workers.bpo.manager.notifier import _build_subject
        assert _build_subject("見積処理", "completed") == "✅ BPO完了: 見積処理"

    def test_approval_needed(self):
        from workers.bpo.manager.notifier import _build_subject
        assert _build_subject("請求処理", "approval_needed") == "🔔 BPO承認待ち: 請求処理"

    def test_error(self):
        from workers.bpo.manager.notifier import _build_subject
        assert _build_subject("安全書類", "error") == "❌ BPOエラー: 安全書類"

    def test_degradation(self):
        from workers.bpo.manager.notifier import _build_subject
        assert _build_subject("品質管理", "degradation") == "⚠️ 精度劣化検知: 品質管理"

    def test_circuit_breaker(self):
        from workers.bpo.manager.notifier import _build_subject
        assert _build_subject("在庫最適化", "circuit_breaker") == "🚨 Circuit Breaker発動: 在庫最適化"

    def test_unknown_event_type(self):
        from workers.bpo.manager.notifier import _build_subject
        subject = _build_subject("テスト", "unknown_event")
        assert "テスト" in subject


# ---------------------------------------------------------------------------
# _build_html_body
# ---------------------------------------------------------------------------

class TestBuildHtmlBody:
    def test_contains_pipeline_name(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("company123", "見積処理", "completed", None)
        assert "見積処理" in body

    def test_contains_company_id_prefix(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("company-abc-xyz", "見積処理", "completed", None)
        # company_id[:8] = "company-" が含まれること
        assert "company-" in body

    def test_details_cost_yen_formatted(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("cid", "P1", "completed", {"cost_yen": 12345.6})
        assert "¥12,346" in body

    def test_details_approval_url_link(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("cid", "P1", "approval_needed", {"approval_url": "https://example.com/approve"})
        assert "https://example.com/approve" in body
        assert "承認画面を開く" in body

    def test_details_error_message(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("cid", "P1", "error", {"error": "DBタイムアウト"})
        assert "DBタイムアウト" in body

    def test_no_details_returns_valid_html(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("cid", "P1", "completed", None)
        assert "<!DOCTYPE html>" in body
        assert "</html>" in body

    def test_xss_escaping_in_pipeline_name(self):
        from workers.bpo.manager.notifier import _build_html_body
        body = _build_html_body("cid", "<script>alert(1)</script>", "completed", None)
        assert "<script>" not in body
        assert "&lt;script&gt;" in body


# ---------------------------------------------------------------------------
# _send_email_notification
# ---------------------------------------------------------------------------

class TestSendEmailNotification:
    @pytest.mark.asyncio
    async def test_sends_to_recipients(self):
        from workers.bpo.manager.notifier import _send_email_notification

        gmail_mock = _make_gmail_mock()
        # notifier.py 内でローカル import しているため、パッチターゲットはモジュール本体
        with patch.dict("os.environ", {"NOTIFICATION_EMAIL_TO": "a@example.com,b@example.com"}):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await _send_email_notification(
                        "company001", "見積処理", "completed", {"cost_yen": 500}
                    )
        assert result is True
        gmail_mock.write_record.assert_called_once()
        call_args = gmail_mock.write_record.call_args
        assert call_args[0][0] == "send"
        assert "a@example.com" in call_args[0][1]["to"]
        assert "b@example.com" in call_args[0][1]["to"]

    @pytest.mark.asyncio
    async def test_returns_false_when_env_not_set(self):
        from workers.bpo.manager.notifier import _send_email_notification

        with patch.dict("os.environ", {}, clear=True):
            result = await _send_email_notification("cid", "P1", "completed", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_gmail_error(self):
        from workers.bpo.manager.notifier import _send_email_notification

        gmail_mock = AsyncMock()
        gmail_mock.write_record = AsyncMock(side_effect=RuntimeError("Gmail API失敗"))
        with patch.dict("os.environ", {"NOTIFICATION_EMAIL_TO": "x@example.com"}):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await _send_email_notification("cid", "P1", "error", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_default_from_address(self):
        from workers.bpo.manager.notifier import _send_email_notification

        gmail_mock = _make_gmail_mock()
        with patch.dict("os.environ", {
            "NOTIFICATION_EMAIL_TO": "x@example.com",
        }, clear=True):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await _send_email_notification("cid", "P1", "completed", None)

        assert result is True


# ---------------------------------------------------------------------------
# _send_slack_notification
# ---------------------------------------------------------------------------

class TestSendSlackNotification:
    @pytest.mark.asyncio
    async def test_sends_slack_when_configured(self):
        from workers.bpo.manager.notifier import _send_slack_notification

        slack_mock = _make_slack_mock()
        env = {
            "SLACK_NOTIFICATION_CHANNEL": "#bpo-alerts",
            "SLACK_BOT_TOKEN": "xoxb-test-token",
        }
        with patch.dict("os.environ", env):
            with patch("workers.connector.slack.SlackConnector", return_value=slack_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await _send_slack_notification("cid", "P1", "completed", None)
        assert result is True
        slack_mock.write_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_channel(self):
        from workers.bpo.manager.notifier import _send_slack_notification

        with patch.dict("os.environ", {}, clear=True):
            result = await _send_slack_notification("cid", "P1", "completed", None)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_slack_error(self):
        from workers.bpo.manager.notifier import _send_slack_notification

        slack_mock = AsyncMock()
        slack_mock.write_record = AsyncMock(side_effect=Exception("Slack接続失敗"))
        env = {
            "SLACK_NOTIFICATION_CHANNEL": "#bpo-alerts",
            "SLACK_BOT_TOKEN": "xoxb-test",
        }
        with patch.dict("os.environ", env):
            with patch("workers.connector.slack.SlackConnector", return_value=slack_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await _send_slack_notification("cid", "P1", "error", None)
        assert result is False


# ---------------------------------------------------------------------------
# notify_pipeline_event (公開API)
# ---------------------------------------------------------------------------

class TestNotifyPipelineEvent:
    @pytest.mark.asyncio
    async def test_log_only_when_no_channels_configured(self):
        """通知チャネルが未設定の場合は log-only で True を返す。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        with patch.dict("os.environ", {}, clear=True):
            result = await notify_pipeline_event("company001", "見積処理", "completed")
        assert result is True

    @pytest.mark.asyncio
    async def test_email_sent_when_configured(self):
        """NOTIFICATION_EMAIL_TO が設定されている場合はメール送信される。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        gmail_mock = _make_gmail_mock()
        with patch.dict("os.environ", {"NOTIFICATION_EMAIL_TO": "admin@example.com"}):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await notify_pipeline_event(
                        "company001", "請求処理", "approval_needed",
                        {"approval_url": "https://app.shachotwo.com/approve/123"}
                    )
        assert result is True
        gmail_mock.write_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_email_and_slack_sent(self):
        """メールとSlackの両方が設定されている場合、両方に通知する。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        gmail_mock = _make_gmail_mock()
        slack_mock = _make_slack_mock()
        env = {
            "NOTIFICATION_EMAIL_TO": "admin@example.com",
            "SLACK_NOTIFICATION_CHANNEL": "#alerts",
            "SLACK_BOT_TOKEN": "xoxb-test",
        }
        with patch.dict("os.environ", env):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.slack.SlackConnector", return_value=slack_mock):
                    with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                        result = await notify_pipeline_event(
                            "company001", "安全書類", "completed"
                        )
        assert result is True
        gmail_mock.write_record.assert_called_once()
        slack_mock.write_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_configured_but_all_fail(self):
        """通知チャネルが設定されているが全て失敗した場合は False を返す。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        gmail_mock = AsyncMock()
        gmail_mock.write_record = AsyncMock(side_effect=RuntimeError("失敗"))
        slack_mock = AsyncMock()
        slack_mock.write_record = AsyncMock(side_effect=RuntimeError("失敗"))
        env = {
            "NOTIFICATION_EMAIL_TO": "admin@example.com",
            "SLACK_NOTIFICATION_CHANNEL": "#alerts",
            "SLACK_BOT_TOKEN": "xoxb-test",
        }
        with patch.dict("os.environ", env):
            with patch("workers.connector.email.GmailConnector", return_value=gmail_mock):
                with patch("workers.connector.slack.SlackConnector", return_value=slack_mock):
                    with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                        result = await notify_pipeline_event(
                            "company001", "P1", "error", {"error": "DBエラー"}
                        )
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event_type", [
        "completed", "approval_needed", "error", "degradation", "circuit_breaker"
    ])
    async def test_all_event_types_do_not_raise(self, event_type: str):
        """全event_typeでnotify_pipeline_eventが例外を上げないこと。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        with patch.dict("os.environ", {}, clear=True):
            result = await notify_pipeline_event("cid", "P1", event_type)
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_details_none_does_not_raise(self):
        from workers.bpo.manager.notifier import notify_pipeline_event

        with patch.dict("os.environ", {}, clear=True):
            result = await notify_pipeline_event("cid", "P1", "completed", None)
        assert result is True

    @pytest.mark.asyncio
    async def test_pipeline_failure_does_not_propagate(self):
        """Gmail完全クラッシュでもnotifyが例外を上げないこと（パイプラインを止めない）。"""
        from workers.bpo.manager.notifier import notify_pipeline_event

        with patch.dict("os.environ", {"NOTIFICATION_EMAIL_TO": "x@example.com"}):
            with patch("workers.connector.email.GmailConnector", side_effect=Exception("予期しないクラッシュ")):
                with patch("workers.connector.base.ConnectorConfig", return_value=MagicMock()):
                    result = await notify_pipeline_event("cid", "P1", "error", {"error": "test"})
        # 例外は上がらず、Falseが返る
        assert result is False

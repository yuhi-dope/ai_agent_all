"""poll_saas_changes — コネクタ連携実装のテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

COMPANY_ID = "test-company-abc"


def _make_db_mock(last_run_data=None, cred_data=None):
    """Supabase DBクライアントのモックを生成する。"""
    db = MagicMock()

    # execution_logs (前回ポーリング時刻)
    last_run_result = MagicMock()
    last_run_result.data = last_run_data or []

    # saas_connections (認証情報)
    cred_result = MagicMock()
    cred_result.data = cred_data or []

    # insert (ポーリング記録保存)
    insert_chain = MagicMock()
    insert_chain.execute.return_value = MagicMock()

    def table_side_effect(name):
        tbl = MagicMock()
        if name == "execution_logs":
            # select → eq → eq → order → limit → execute の chain
            tbl.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = last_run_result
            tbl.insert.return_value.execute.return_value = MagicMock()
        elif name == "saas_connections":
            tbl.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = cred_result
        return tbl

    db.table.side_effect = table_side_effect
    return db


# ─── 接続情報なし ─────────────────────────────────────────────────────────────

class TestPollSaasChangesNoConnection:
    @pytest.mark.asyncio
    async def test_returns_empty_when_not_connected(self):
        """saas_connectionsにレコードがない場合は空リストを返す。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        db = _make_db_mock(cred_data=[])

        with patch("workers.bpo.manager.event_listener.get_service_client", return_value=db, create=True):
            with patch("db.supabase.get_service_client", return_value=db):
                result = await poll_saas_changes(COMPANY_ID, "freee")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        """例外が発生した場合は空リストを返し、エラーをログに残す。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        with patch("db.supabase.get_service_client", side_effect=RuntimeError("DB down")):
            result = await poll_saas_changes(COMPANY_ID, "freee")

        assert result == []


# ─── freee ───────────────────────────────────────────────────────────────────

class TestPollFreee:
    @pytest.mark.asyncio
    async def test_overdue_invoice_creates_task(self):
        """freeeの期限超過請求書はfreee.invoice.overdueタスクを生成する。"""
        from workers.bpo.manager.event_listener import poll_saas_changes
        from workers.bpo.manager.models import BPOTask, TriggerType

        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(cred_data=cred)

        overdue_record = {"id": "inv-1", "payment_status": "overdue", "amount": 50000}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[overdue_record])

        dummy_task = BPOTask(
            company_id=COMPANY_ID,
            pipeline="construction/billing",
            trigger_type=TriggerType.EVENT,
            input_data=overdue_record,
        )

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=dummy_task),
                ) as mock_hw:
                    result = await poll_saas_changes(COMPANY_ID, "freee")

        mock_hw.assert_awaited_once_with(COMPANY_ID, "freee.invoice.overdue", overdue_record)
        assert len(result) == 1
        assert result[0] is dummy_task

    @pytest.mark.asyncio
    async def test_paid_invoice_creates_task(self):
        """freeeの支払済み請求書はfreee.invoice.paidタスクを生成する。"""
        from workers.bpo.manager.event_listener import poll_saas_changes
        from workers.bpo.manager.models import BPOTask, TriggerType

        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(cred_data=cred)

        paid_record = {"id": "inv-2", "payment_status": "paid", "amount": 30000}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[paid_record])

        dummy_task = BPOTask(
            company_id=COMPANY_ID,
            pipeline="construction/billing",
            trigger_type=TriggerType.EVENT,
            input_data=paid_record,
        )

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=dummy_task),
                ) as mock_hw:
                    result = await poll_saas_changes(COMPANY_ID, "freee")

        mock_hw.assert_awaited_once_with(COMPANY_ID, "freee.invoice.paid", paid_record)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unknown_status_skips(self):
        """freeeの不明なステータスはタスクを生成しない。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(cred_data=cred)

        unknown_record = {"id": "inv-3", "payment_status": "pending", "amount": 10000}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[unknown_record])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=None),
                ):
                    result = await poll_saas_changes(COMPANY_ID, "freee")

        assert result == []

    @pytest.mark.asyncio
    async def test_since_filter_passed_when_last_run_exists(self):
        """前回ポーリング記録がある場合、sinceフィルタがコネクタに渡される。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        last_run = [{"created_at": "2026-03-27T10:00:00+00:00"}]
        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(last_run_data=last_run, cred_data=cred)

        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                await poll_saas_changes(COMPANY_ID, "freee")

        connector.read_records.assert_awaited_once_with(
            "invoices", {"since": "2026-03-27T10:00:00+00:00"}
        )

    @pytest.mark.asyncio
    async def test_no_since_filter_when_first_run(self):
        """初回ポーリング（前回記録なし）はsinceフィルタなしでコネクタを呼ぶ。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(last_run_data=[], cred_data=cred)

        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                await poll_saas_changes(COMPANY_ID, "freee")

        connector.read_records.assert_awaited_once_with("invoices", {})


# ─── kintone ─────────────────────────────────────────────────────────────────

class TestPollKintone:
    @pytest.mark.asyncio
    async def test_record_creates_task_with_status_event_type(self):
        """kintoneのレコードはstatusフィールドに基づくイベントタイプでタスクを生成する。"""
        from workers.bpo.manager.event_listener import poll_saas_changes
        from workers.bpo.manager.models import BPOTask, TriggerType

        cred = [{"encrypted_credentials": {"api_key": "key"}}]
        db = _make_db_mock(cred_data=cred)

        rec = {"id": "rec-1", "status": "approved", "title": "発注申請"}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[rec])

        dummy_task = BPOTask(
            company_id=COMPANY_ID,
            pipeline="construction/estimation",
            trigger_type=TriggerType.EVENT,
            input_data=rec,
        )

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=dummy_task),
                ) as mock_hw:
                    result = await poll_saas_changes(COMPANY_ID, "kintone")

        mock_hw.assert_awaited_once_with(COMPANY_ID, "kintone.record.approved", rec)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_record_without_status_defaults_to_updated(self):
        """kintoneのstatusなしレコードはkintone.record.updatedになる。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"api_key": "key"}}]
        db = _make_db_mock(cred_data=cred)

        rec = {"id": "rec-2", "title": "ステータスなし"}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[rec])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=None),
                ) as mock_hw:
                    await poll_saas_changes(COMPANY_ID, "kintone")

        mock_hw.assert_awaited_once_with(COMPANY_ID, "kintone.record.updated", rec)


# ─── slack ────────────────────────────────────────────────────────────────────

class TestPollSlack:
    @pytest.mark.asyncio
    async def test_problem_message_creates_ticket_task(self):
        """'問題'を含むSlackメッセージはticket_createdタスクを生成する。"""
        from workers.bpo.manager.event_listener import poll_saas_changes
        from workers.bpo.manager.models import BPOTask, TriggerType

        cred = [{"encrypted_credentials": {"bot_token": "xoxb"}}]
        db = _make_db_mock(cred_data=cred)

        msg = {"text": "システムに問題が発生しました", "channel": "C001", "user": "U001", "ts": "1234.5678"}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[msg])

        dummy_task = BPOTask(
            company_id=COMPANY_ID,
            pipeline="cs/support_auto_response",
            trigger_type=TriggerType.EVENT,
            input_data=msg,
        )

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=dummy_task),
                ) as mock_hw:
                    result = await poll_saas_changes(COMPANY_ID, "slack")

        mock_hw.assert_awaited_once_with(COMPANY_ID, "ticket_created", {
            "source": "slack",
            "text": "システムに問題が発生しました",
            "channel": "C001",
            "user": "U001",
            "ts": "1234.5678",
        })
        assert len(result) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("keyword", ["エラー", "不具合", "help", "質問"])
    async def test_all_trigger_keywords_detected(self, keyword: str):
        """全トリガーキーワードでticket_createdが生成される。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"bot_token": "xoxb"}}]
        db = _make_db_mock(cred_data=cred)

        msg = {"text": f"{keyword}があります", "channel": "C002", "user": "U002", "ts": "9999.0"}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[msg])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=None),
                ) as mock_hw:
                    await poll_saas_changes(COMPANY_ID, "slack")

        mock_hw.assert_awaited_once()
        call_args = mock_hw.call_args[0]
        assert call_args[1] == "ticket_created"

    @pytest.mark.asyncio
    async def test_normal_message_skipped(self):
        """通常のSlackメッセージ（キーワードなし）はタスクを生成しない。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"bot_token": "xoxb"}}]
        db = _make_db_mock(cred_data=cred)

        msg = {"text": "今日のランチはラーメン", "channel": "C003", "user": "U003", "ts": "1111.0"}
        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[msg])

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                with patch(
                    "workers.bpo.manager.event_listener.handle_webhook",
                    new=AsyncMock(return_value=None),
                ) as mock_hw:
                    result = await poll_saas_changes(COMPANY_ID, "slack")

        mock_hw.assert_not_awaited()
        assert result == []


# ─── ポーリング記録の保存 ──────────────────────────────────────────────────────

class TestPollLogging:
    @pytest.mark.asyncio
    async def test_execution_log_saved_after_polling(self):
        """ポーリング完了後にexecution_logsへ記録が保存される。"""
        from workers.bpo.manager.event_listener import poll_saas_changes

        cred = [{"encrypted_credentials": {"token": "tok"}}]
        db = _make_db_mock(cred_data=cred)

        connector = MagicMock()
        connector.read_records = AsyncMock(return_value=[])

        insert_mock = MagicMock()
        insert_mock.execute = MagicMock(return_value=MagicMock())

        # execution_logsのinsertを個別にキャプチャ
        insert_calls = []

        original_side_effect = db.table.side_effect

        def table_with_insert_spy(name):
            tbl = original_side_effect(name)
            if name == "execution_logs":
                original_insert = tbl.insert

                def spy_insert(data):
                    insert_calls.append(data)
                    return insert_mock

                tbl.insert = spy_insert
            return tbl

        db.table.side_effect = table_with_insert_spy

        with patch("db.supabase.get_service_client", return_value=db):
            with patch("workers.connector.factory.get_connector", return_value=connector):
                await poll_saas_changes(COMPANY_ID, "freee")

        assert len(insert_calls) == 1
        log_data = insert_calls[0]
        assert log_data["company_id"] == COMPANY_ID
        assert log_data["action_type"] == "poll_freee"
        assert log_data["operations"]["service"] == "freee"
        assert "records_found" in log_data["operations"]

"""GWS逆同期エンジンのユニットテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from workers.gws import sync_engine


class TestSyncPipelineResult:
    @pytest.mark.asyncio
    async def test_proposal_triggers_drive_sync(self):
        """提案書生成完了時にDrive同期が実行される。"""
        result = SimpleNamespace(final_output={
            "pdf_b64": "dGVzdA==",
            "company_name": "テスト社",
            "proposal_id": "prop1",
        })

        with patch.object(sync_engine, "_sync_proposal_to_drive", new_callable=AsyncMock, return_value=True) as mock_drive, \
             patch.object(sync_engine, "_sync_proposal_link_to_calendar", new_callable=AsyncMock, return_value=False):
            synced = await sync_engine.sync_pipeline_result(
                "company123", "proposal_generation_pipeline", result
            )

        mock_drive.assert_awaited_once()
        assert "drive:proposal" in synced["synced"]

    @pytest.mark.asyncio
    async def test_outreach_triggers_sheets_sync(self):
        """アウトリーチ完了時にSheets同期が実行される。"""
        result = SimpleNamespace(final_output={
            "sent_count": 50,
        })

        with patch.object(sync_engine, "_sync_outreach_to_sheets", new_callable=AsyncMock, return_value=True) as mock:
            synced = await sync_engine.sync_pipeline_result(
                "company123", "outreach_pipeline", result
            )

        mock.assert_awaited_once()
        assert "sheets:outreach_status" in synced["synced"]

    @pytest.mark.asyncio
    async def test_support_triggers_gmail_draft(self):
        """サポート自動応答完了時にGmail下書きが生成される。"""
        result = SimpleNamespace(final_output={
            "reply_subject": "Re: お問い合わせ",
            "reply_body": "<p>回答です</p>",
            "customer_email": "customer@example.com",
        })

        with patch.object(sync_engine, "_sync_support_draft", new_callable=AsyncMock, return_value=True) as mock:
            synced = await sync_engine.sync_pipeline_result(
                "company123", "support_auto_response_pipeline", result
            )

        mock.assert_awaited_once()
        assert "gmail:support_draft" in synced["synced"]

    @pytest.mark.asyncio
    async def test_unknown_pipeline_returns_empty(self):
        """未知のパイプラインは空の同期リストを返す。"""
        synced = await sync_engine.sync_pipeline_result(
            "company123", "unknown_pipeline", None
        )
        assert synced["synced"] == []

    @pytest.mark.asyncio
    async def test_followup_mode_triggers_draft(self):
        """customer_lifecycle(followup)でGmail下書きが生成される。"""
        result = SimpleNamespace(final_output={
            "mode": "followup",
            "followup_subject": "本日はありがとうございました",
            "followup_body": "<p>フォローアップ</p>",
            "contact_email": "contact@example.com",
        })

        with patch.object(sync_engine, "_sync_followup_draft", new_callable=AsyncMock, return_value=True) as mock:
            synced = await sync_engine.sync_pipeline_result(
                "company123", "customer_lifecycle_pipeline", result
            )

        mock.assert_awaited_once()
        assert "gmail:followup_draft" in synced["synced"]


class TestExtractOutput:
    def test_dict_passthrough(self):
        assert sync_engine._extract_output({"key": "val"}) == {"key": "val"}

    def test_none_returns_empty(self):
        assert sync_engine._extract_output(None) == {}

    def test_final_output_attr(self):
        obj = SimpleNamespace(final_output={"data": 1})
        assert sync_engine._extract_output(obj) == {"data": 1}

    def test_result_attr(self):
        obj = SimpleNamespace(result={"data": 2})
        assert sync_engine._extract_output(obj) == {"data": 2}


class TestMakeSyncId:
    def test_deterministic(self):
        id1 = sync_engine._make_sync_id("c1", "drive:proposal", "p1")
        id2 = sync_engine._make_sync_id("c1", "drive:proposal", "p1")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        id1 = sync_engine._make_sync_id("c1", "drive:proposal", "p1")
        id2 = sync_engine._make_sync_id("c1", "drive:proposal", "p2")
        assert id1 != id2


class TestRunPendingSyncs:
    """run_pending_syncs() のリトライロジックのテスト。"""

    def _make_db_mock(self, pending_records=None, failed_records=None):
        """Supabase クライアントのモックを構築する。"""
        db = MagicMock()
        # pending クエリ
        pending_result = MagicMock()
        pending_result.data = pending_records or []
        # failed クエリ
        failed_result = MagicMock()
        failed_result.data = failed_records or []

        def select_side_effect(*args, **kwargs):
            return db._select_chain

        db._select_chain = MagicMock()
        db._select_chain.eq.return_value = db._select_chain
        db._select_chain.lt.return_value = db._select_chain
        db._select_chain.limit.return_value = db._select_chain

        # eq("status", "pending") の場合 pending_result, "failed" の場合 failed_result を返す
        call_count = {"n": 0}

        def execute_side():
            call_count["n"] += 1
            # 1回目: pending, 2回目: failed
            if call_count["n"] == 1:
                return pending_result
            return failed_result

        db._select_chain.execute.side_effect = execute_side
        db.table.return_value.select.return_value = db._select_chain

        # update チェーン
        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()
        db.table.return_value.update.return_value = update_chain

        return db

    @pytest.mark.asyncio
    async def test_pending_record_success(self):
        """pending レコードをハンドラが成功処理すると status=synced になる。"""
        record = {
            "id": "rec1",
            "company_id": "company123",
            "sync_type": "outreach_to_sheets",
            "retry_count": 0,
            "payload": {"sent_count": 10},
            "next_retry_at": None,
        }
        db_mock = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.limit.return_value = select_chain

        call_count = {"n": 0}
        def execute_side():
            call_count["n"] += 1
            res = MagicMock()
            res.data = [record] if call_count["n"] == 1 else []
            return res

        select_chain.execute.side_effect = execute_side
        db_mock.table.return_value.select.return_value = select_chain
        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()
        db_mock.table.return_value.update.return_value = update_chain

        with patch("db.supabase.get_service_client", return_value=db_mock), \
             patch.object(sync_engine, "_sync_outreach_to_sheets", new_callable=AsyncMock, return_value=True):
            result = await sync_engine.run_pending_syncs("company123")

        assert result == 1
        # update が status=synced で呼ばれたことを確認
        update_call_args = db_mock.table.return_value.update.call_args_list
        assert any(
            "synced" in str(call) for call in update_call_args
        ), f"synced update not found in {update_call_args}"

    @pytest.mark.asyncio
    async def test_pending_record_failure_increments_retry(self):
        """ハンドラが失敗すると retry_count が増加し status=failed になる。"""
        record = {
            "id": "rec2",
            "company_id": "company123",
            "sync_type": "proposal_to_drive",
            "retry_count": 0,
            "payload": {},
            "next_retry_at": None,
        }
        db_mock = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.limit.return_value = select_chain

        call_count = {"n": 0}
        def execute_side():
            call_count["n"] += 1
            res = MagicMock()
            res.data = [record] if call_count["n"] == 1 else []
            return res

        select_chain.execute.side_effect = execute_side
        db_mock.table.return_value.select.return_value = select_chain
        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()
        db_mock.table.return_value.update.return_value = update_chain

        with patch("db.supabase.get_service_client", return_value=db_mock), \
             patch.object(sync_engine, "_sync_proposal_to_drive", new_callable=AsyncMock, return_value=False):
            result = await sync_engine.run_pending_syncs("company123")

        assert result == 0
        # update が retry_count=1, status=failed で呼ばれたことを確認
        update_call_args = db_mock.table.return_value.update.call_args_list
        assert any(
            "failed" in str(call) and "retry_count" in str(call)
            for call in update_call_args
        ), f"failed retry update not found in {update_call_args}"

    @pytest.mark.asyncio
    async def test_max_retry_becomes_permanently_failed(self):
        """retry_count が MAX_RETRY_COUNT に達すると permanently_failed になる。"""
        record = {
            "id": "rec3",
            "company_id": "company123",
            "sync_type": "support_draft",
            "retry_count": 2,  # MAX_RETRY_COUNT - 1
            "payload": {},
            "next_retry_at": None,
        }
        db_mock = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.limit.return_value = select_chain

        call_count = {"n": 0}
        def execute_side():
            call_count["n"] += 1
            res = MagicMock()
            res.data = [record] if call_count["n"] == 1 else []
            return res

        select_chain.execute.side_effect = execute_side
        db_mock.table.return_value.select.return_value = select_chain
        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock()
        db_mock.table.return_value.update.return_value = update_chain

        with patch("db.supabase.get_service_client", return_value=db_mock), \
             patch.object(sync_engine, "_sync_support_draft", new_callable=AsyncMock, return_value=False):
            result = await sync_engine.run_pending_syncs("company123")

        assert result == 0
        update_call_args = db_mock.table.return_value.update.call_args_list
        assert any(
            "permanently_failed" in str(call)
            for call in update_call_args
        ), f"permanently_failed not found in {update_call_args}"

    @pytest.mark.asyncio
    async def test_no_records_returns_zero(self):
        """処理対象レコードがない場合は 0 を返す。"""
        db_mock = MagicMock()
        select_chain = MagicMock()
        select_chain.eq.return_value = select_chain
        select_chain.lt.return_value = select_chain
        select_chain.limit.return_value = select_chain
        res = MagicMock()
        res.data = []
        select_chain.execute.return_value = res
        db_mock.table.return_value.select.return_value = select_chain

        with patch("db.supabase.get_service_client", return_value=db_mock):
            result = await sync_engine.run_pending_syncs("company123")

        assert result == 0

    def test_is_retry_due_null_next_retry(self):
        """next_retry_at が None のレコードは常にリトライ対象。"""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        record = {"next_retry_at": None}
        assert sync_engine._is_retry_due(record, now) is True

    def test_is_retry_due_past_time(self):
        """next_retry_at が過去なら True を返す。"""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        past = (now - timedelta(minutes=10)).isoformat()
        record = {"next_retry_at": past}
        assert sync_engine._is_retry_due(record, now) is True

    def test_is_retry_due_future_time(self):
        """next_retry_at が未来なら False を返す。"""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        future = (now + timedelta(minutes=10)).isoformat()
        record = {"next_retry_at": future}
        assert sync_engine._is_retry_due(record, now) is False


class TestEnqueuePendingSync:
    """_enqueue_pending_sync() のテスト。"""

    @pytest.mark.asyncio
    async def test_inserts_pending_record(self):
        """同期失敗時に pending レコードが INSERT される。"""
        db_mock = MagicMock()
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock()
        db_mock.table.return_value.insert.return_value = insert_chain

        with patch("db.supabase.get_service_client", return_value=db_mock):
            await sync_engine._enqueue_pending_sync(
                "company123",
                "proposal_to_drive",
                "proposal_generation_pipeline",
                {"pdf_b64": "dGVzdA=="},
            )

        db_mock.table.assert_called_with("gws_sync_state")
        insert_call = db_mock.table.return_value.insert.call_args[0][0]
        assert insert_call["status"] == "pending"
        assert insert_call["retry_count"] == 0
        assert insert_call["sync_type"] == "proposal_to_drive"
        assert insert_call["company_id"] == "company123"

    @pytest.mark.asyncio
    async def test_sync_pipeline_result_enqueues_on_failure(self):
        """sync_pipeline_result が同期失敗時に _enqueue_pending_sync を呼ぶ。"""
        from types import SimpleNamespace

        result = SimpleNamespace(final_output={"sent_count": 5})

        with patch.object(sync_engine, "_sync_outreach_to_sheets", new_callable=AsyncMock, return_value=False), \
             patch.object(sync_engine, "_enqueue_pending_sync", new_callable=AsyncMock) as mock_enqueue:
            await sync_engine.sync_pipeline_result(
                "company123", "outreach_pipeline", result
            )

        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.call_args
        assert call_kwargs[0][1] == "outreach_to_sheets"

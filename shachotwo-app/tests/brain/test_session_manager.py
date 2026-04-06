"""Tests for brain/knowledge/session_manager.py

外部API（Supabase, LLM）は全てモック。
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.knowledge.session_manager import (
    COMPACT_THRESHOLD,
    _estimate_coverage_rate,
    _extract_summary,
    _resolve_display_status,
    auto_compact,
    get_or_create_theme_session,
    get_session_context_for_llm,
    get_theme_progress,
    record_qa_turn,
)

COMPANY_ID = str(uuid4())
USER_ID = str(uuid4())
SESSION_ID = str(uuid4())


# ---------------------------------------------------------------------------
# _extract_summary
# ---------------------------------------------------------------------------

class TestExtractSummary:
    def test_json_list_summary(self):
        raw = json.dumps({"summary": ["ルールA", "数値B: 100万円"]})
        result = _extract_summary(raw)
        assert "- ルールA" in result
        assert "- 数値B: 100万円" in result

    def test_json_string_summary(self):
        raw = json.dumps({"summary": "単一要約テキスト"})
        result = _extract_summary(raw)
        assert result == "単一要約テキスト"

    def test_plain_text_fallback(self):
        raw = "JSONではない要約テキスト"
        result = _extract_summary(raw)
        assert result == "JSONではない要約テキスト"

    def test_code_fence_stripped(self):
        raw = "```json\n{\"summary\": [\"要約1\"]}\n```"
        result = _extract_summary(raw)
        assert "- 要約1" in result

    def test_empty_string(self):
        result = _extract_summary("")
        assert result == ""


# ---------------------------------------------------------------------------
# _estimate_coverage_rate
# ---------------------------------------------------------------------------

class TestEstimateCoverageRate:
    def test_zero_questions(self):
        assert _estimate_coverage_rate(0, "active") == 0.0

    def test_half_threshold(self):
        rate = _estimate_coverage_rate(COMPACT_THRESHOLD // 2, "active")
        assert 0.0 < rate < 0.8

    def test_at_threshold(self):
        rate = _estimate_coverage_rate(COMPACT_THRESHOLD, "active")
        assert rate == 0.8

    def test_compacted_bonus(self):
        rate_active = _estimate_coverage_rate(COMPACT_THRESHOLD, "active")
        rate_compacted = _estimate_coverage_rate(COMPACT_THRESHOLD, "compacted")
        assert rate_compacted > rate_active

    def test_caps_at_one(self):
        rate = _estimate_coverage_rate(COMPACT_THRESHOLD * 10, "compacted")
        assert rate <= 1.0


# ---------------------------------------------------------------------------
# _resolve_display_status
# ---------------------------------------------------------------------------

class TestResolveDisplayStatus:
    def test_not_started(self):
        assert _resolve_display_status("active", 0) == "not_started"

    def test_not_started_explicit(self):
        assert _resolve_display_status("not_started", 5) == "not_started"

    def test_active_in_progress(self):
        assert _resolve_display_status("active", 5) == "active"

    def test_compacted_is_completed(self):
        assert _resolve_display_status("compacted", 10) == "completed"

    def test_closed_is_completed(self):
        assert _resolve_display_status("closed", 10) == "completed"

    def test_at_threshold_is_completed(self):
        assert _resolve_display_status("active", COMPACT_THRESHOLD) == "completed"


# ---------------------------------------------------------------------------
# get_or_create_theme_session
# ---------------------------------------------------------------------------

class TestGetOrCreateThemeSession:
    @pytest.mark.asyncio
    async def test_returns_existing_session(self):
        existing = {
            "id": SESSION_ID,
            "company_id": COMPANY_ID,
            "bpo_theme": "製造",
            "session_status": "active",
            "question_count": 3,
        }
        mock_db = MagicMock()
        (
            mock_db.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.return_value
            .order.return_value
            .limit.return_value
            .execute.return_value
        ) = MagicMock(data=[existing])

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            result = await get_or_create_theme_session(COMPANY_ID, USER_ID, "製造")

        assert result["id"] == SESSION_ID
        assert result["bpo_theme"] == "製造"

    @pytest.mark.asyncio
    async def test_creates_new_session_when_none_exists(self):
        new_session = {
            "id": str(uuid4()),
            "company_id": COMPANY_ID,
            "bpo_theme": "品質管理",
            "session_status": "active",
            "question_count": 0,
        }
        mock_db = MagicMock()
        # 検索結果が空
        (
            mock_db.table.return_value
            .select.return_value
            .eq.return_value
            .eq.return_value
            .eq.return_value
            .order.return_value
            .limit.return_value
            .execute.return_value
        ) = MagicMock(data=[])
        # insert結果
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[new_session]
        )

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            result = await get_or_create_theme_session(COMPANY_ID, USER_ID, "品質管理")

        assert result["bpo_theme"] == "品質管理"
        mock_db.table.return_value.insert.assert_called_once()


# ---------------------------------------------------------------------------
# record_qa_turn
# ---------------------------------------------------------------------------

class TestRecordQaTurn:
    def _make_mock_db(self, current_count: int, archive: list) -> MagicMock:
        """DBモックを構築するヘルパー。"""
        session = {
            "id": SESSION_ID,
            "company_id": COMPANY_ID,
            "question_count": current_count,
            "raw_context_archive": archive,
            "session_status": "active",
        }
        updated_session = {**session, "question_count": current_count + 1}

        mock_db = MagicMock()
        # select().eq().single().execute() → 現在のセッション
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)
        # update().eq().execute() → 更新後セッション
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[updated_session])
        return mock_db

    @pytest.mark.asyncio
    async def test_increments_question_count(self):
        mock_db = self._make_mock_db(current_count=2, archive=[])

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            result = await record_qa_turn(SESSION_ID, "質問1", "回答1")

        assert result["question_count"] == 3

    @pytest.mark.asyncio
    async def test_triggers_auto_compact_when_over_threshold(self):
        mock_db = self._make_mock_db(current_count=COMPACT_THRESHOLD, archive=[])

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch("brain.knowledge.session_manager.auto_compact", new_callable=AsyncMock) as mock_compact:
            # 圧縮後の再取得
            mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
                data={
                    "id": SESSION_ID,
                    "company_id": COMPANY_ID,
                    "question_count": COMPACT_THRESHOLD + 1,
                    "raw_context_archive": [],
                    "session_status": "active",
                }
            )
            await record_qa_turn(SESSION_ID, "質問", "回答")

        mock_compact.assert_called_once_with(SESSION_ID)

    @pytest.mark.asyncio
    async def test_no_compact_below_threshold(self):
        mock_db = self._make_mock_db(current_count=COMPACT_THRESHOLD - 2, archive=[])

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch("brain.knowledge.session_manager.auto_compact", new_callable=AsyncMock) as mock_compact:
            await record_qa_turn(SESSION_ID, "質問", "回答")

        mock_compact.assert_not_called()


# ---------------------------------------------------------------------------
# auto_compact
# ---------------------------------------------------------------------------

class TestAutoCompact:
    @pytest.mark.asyncio
    async def test_compacts_and_resets_archive(self):
        archive = [
            {"q": "製造ラインの休止基準は？", "a": "不良率0.5%超でライン停止"},
            {"q": "5S活動の頻度は？", "a": "週1回15分"},
        ]
        session = {
            "id": SESSION_ID,
            "company_id": COMPANY_ID,
            "raw_context_archive": archive,
            "compressed_context": None,
        }

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=MagicMock(
            content=json.dumps({"summary": ["不良率0.5%超でライン停止", "5S: 週1回15分"]}),
            model_used="gemini-flash",
            cost_yen=0.01,
        ))

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch("brain.knowledge.session_manager.get_llm_client", return_value=mock_llm):
            await auto_compact(SESSION_ID)

        # updateが2回呼ばれること（status=compacted → active）
        assert mock_db.table.return_value.update.call_count == 2
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_archive_empty(self):
        session = {
            "id": SESSION_ID,
            "company_id": COMPANY_ID,
            "raw_context_archive": [],
            "compressed_context": None,
        }
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock()

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch("brain.knowledge.session_manager.get_llm_client", return_value=mock_llm):
            await auto_compact(SESSION_ID)

        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_prepends_prior_compressed_context(self):
        """既存のcompressed_contextが次回の要約に引き継がれること。"""
        archive = [{"q": "Q1", "a": "A1"}]
        session = {
            "id": SESSION_ID,
            "company_id": COMPANY_ID,
            "raw_context_archive": archive,
            "compressed_context": "前回の要約テキスト",
        }
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=MagicMock(
            content=json.dumps({"summary": ["統合要約"]}),
            model_used="gemini-flash",
            cost_yen=0.01,
        ))

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch("brain.knowledge.session_manager.get_llm_client", return_value=mock_llm):
            await auto_compact(SESSION_ID)

        call_args = mock_llm.generate.call_args[0][0]
        user_message = call_args.messages[1]["content"]
        assert "前回の要約テキスト" in user_message


# ---------------------------------------------------------------------------
# get_session_context_for_llm
# ---------------------------------------------------------------------------

class TestGetSessionContextForLlm:
    @pytest.mark.asyncio
    async def test_returns_full_archive_when_not_compacted(self):
        archive = [
            {"q": "質問A", "a": "回答A"},
            {"q": "質問B", "a": "回答B"},
        ]
        session = {
            "compressed_context": None,
            "raw_context_archive": archive,
            "question_count": 2,
        }
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            ctx = await get_session_context_for_llm(SESSION_ID)

        assert "質問A" in ctx
        assert "質問B" in ctx
        assert "[これまでの要約]" not in ctx

    @pytest.mark.asyncio
    async def test_returns_summary_plus_recent_when_compacted(self):
        archive = [
            {"q": f"Q{i}", "a": f"A{i}"} for i in range(5)
        ]
        session = {
            "compressed_context": "これが要約です",
            "raw_context_archive": archive,
            "question_count": 15,
        }
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            ctx = await get_session_context_for_llm(SESSION_ID)

        assert "これが要約です" in ctx
        assert "[これまでの要約]" in ctx
        assert "[直近の会話]" in ctx
        # 直近3ターン(Q2,Q3,Q4)が含まれる
        assert "Q4" in ctx
        # Q0,Q1は含まれない
        assert "Q0" not in ctx

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_data(self):
        session = {
            "compressed_context": None,
            "raw_context_archive": [],
            "question_count": 0,
        }
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data=session)

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db):
            ctx = await get_session_context_for_llm(SESSION_ID)

        assert ctx == ""


# ---------------------------------------------------------------------------
# get_theme_progress
# ---------------------------------------------------------------------------

class TestGetThemeProgress:
    @pytest.mark.asyncio
    async def test_returns_progress_for_all_themes(self):
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {"bpo_theme": "製造", "question_count": 12, "session_status": "compacted"},
                {"bpo_theme": "品質管理", "question_count": 5, "session_status": "active"},
            ]
        )

        themes = ["製造", "品質管理", "生産管理"]

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch(
                 "brain.knowledge.session_manager._get_themes_for_industry",
                 new_callable=AsyncMock,
                 return_value=themes,
             ):
            result = await get_theme_progress(COMPANY_ID, "manufacturing")

        assert len(result) == 3

        mfg = next(r for r in result if r["theme"] == "製造")
        assert mfg["question_count"] == 12
        assert mfg["status"] == "completed"

        qa = next(r for r in result if r["theme"] == "品質管理")
        assert qa["question_count"] == 5
        assert qa["status"] == "active"

        prod = next(r for r in result if r["theme"] == "生産管理")
        assert prod["question_count"] == 0
        assert prod["status"] == "not_started"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_genome(self):
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value.execute.return_value = MagicMock(data=[])

        with patch("brain.knowledge.session_manager.get_service_client", return_value=mock_db), \
             patch(
                 "brain.knowledge.session_manager._get_themes_for_industry",
                 new_callable=AsyncMock,
                 return_value=[],
             ):
            result = await get_theme_progress(COMPANY_ID, "unknown_industry")

        assert result == []

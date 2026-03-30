"""Tests for brain/inference module (accuracy monitor, prompt optimizer, improvement cycle, feedback API)."""
from __future__ import annotations
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from brain.inference.accuracy_monitor import get_accuracy_report, StepAccuracyReport
from brain.inference.prompt_optimizer import optimize_prompt
from brain.inference.improvement_cycle import run_improvement_cycle
from routers.accuracy import router
from routers.execution import router as execution_router


# ─────────────────────────────────────
# 共通フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
EXEC_ID = str(uuid.uuid4())

ADMIN_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)

EDITOR_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="editor",
    email="editor@example.com",
)


def _make_mock_db(rows: list[dict] | None = None):
    """supabase クライアントのメソッドチェーンをモックする。"""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.data = rows or []

    chain = mock_db.table.return_value
    for method in ("select", "eq", "gte", "lte", "update", "insert", "neq"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    return mock_db


def _make_exec_row(pipeline: str, steps: list[dict], feedback: dict | None = None) -> dict:
    """execution_logs の1行を生成するヘルパー。"""
    ops: dict = {"pipeline": pipeline, "steps": steps}
    if feedback:
        ops["feedback"] = feedback
    return {"operations": ops, "overall_success": True, "created_at": "2026-03-18T00:00:00Z"}


# ─────────────────────────────────────
# TestGetAccuracyReport
# ─────────────────────────────────────

class TestGetAccuracyReport:

    @pytest.mark.asyncio
    async def test_aggregates_correctly(self):
        """execution_logs から正しく集計できる（high / low / フィードバック あり）。"""
        rows = [
            _make_exec_row("construction/estimation", [
                {"step": "extract", "confidence": 0.9},
            ]),
            _make_exec_row("construction/estimation", [
                {"step": "extract", "confidence": 0.6},   # low confidence
            ], feedback={"rating": "bad"}),
            _make_exec_row("construction/estimation", [
                {"step": "extract", "confidence": 0.5},   # low confidence
            ]),
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        assert len(reports) == 1
        r = reports[0]
        assert r.pipeline == "construction/estimation"
        assert r.step_name == "extract"
        assert r.call_count == 3
        assert r.low_confidence_count == 2   # 0.6, 0.5 が < 0.8
        assert r.feedback_negative_count == 1
        # avg = (0.9 + 0.6 + 0.5) / 3 = 0.6667
        assert r.avg_confidence == round((0.9 + 0.6 + 0.5) / 3, 4)

    @pytest.mark.asyncio
    async def test_needs_improvement_flag(self):
        """needs_improvement が avg<0.75 かつ count>=5 のとき True。"""
        # 5件とも confidence 0.7 → avg 0.7 < 0.75, count=5 → needs_improvement=True
        steps = [{"step": "classify", "confidence": 0.7}]
        rows = [_make_exec_row("dental/claim", steps) for _ in range(5)]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        assert len(reports) == 1
        assert reports[0].needs_improvement is True

    @pytest.mark.asyncio
    async def test_needs_improvement_false_when_count_insufficient(self):
        """count < 5 の場合 needs_improvement は False。"""
        steps = [{"step": "classify", "confidence": 0.5}]
        rows = [_make_exec_row("dental/claim", steps) for _ in range(4)]  # 4件だけ
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        assert reports[0].needs_improvement is False

    @pytest.mark.asyncio
    async def test_company_id_filter_passed_to_db(self):
        """company_id が DB クエリの eq() に渡される。"""
        mock_db = _make_mock_db([])
        chain = mock_db.table.return_value

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            await get_accuracy_report(company_id=COMPANY_ID, days=7)

        chain.eq.assert_called_once_with("company_id", COMPANY_ID)

    @pytest.mark.asyncio
    async def test_no_company_id_skips_filter(self):
        """company_id=None のとき eq() は呼ばれない。"""
        mock_db = _make_mock_db([])
        chain = mock_db.table.return_value

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            await get_accuracy_report(company_id=None)

        chain.eq.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        """データなしで空リストを返す。"""
        mock_db = _make_mock_db([])

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        assert reports == []

    @pytest.mark.asyncio
    async def test_steps_without_confidence_are_ignored(self):
        """confidence キーがない step は集計対象外（ただし行自体はエラーにならない）。"""
        rows = [
            _make_exec_row("mfg/bom", [{"step": "parse"}]),  # confidence なし
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        assert reports == []

    @pytest.mark.asyncio
    async def test_sorted_by_avg_confidence_ascending(self):
        """レポートは avg_confidence 昇順でソートされる。"""
        rows = [
            _make_exec_row("pipe/a", [{"step": "s1", "confidence": 0.9}]),
            _make_exec_row("pipe/b", [{"step": "s2", "confidence": 0.5}]),
            _make_exec_row("pipe/c", [{"step": "s3", "confidence": 0.7}]),
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report()

        avgs = [r.avg_confidence for r in reports]
        assert avgs == sorted(avgs)


# ─────────────────────────────────────
# TestOptimizePrompt
# ─────────────────────────────────────

class TestOptimizePrompt:

    @pytest.mark.asyncio
    async def test_extracts_prompt_block(self):
        """LLMが ```prompt...``` ブロックを返した場合、その中身を抽出する。"""
        mock_response = MagicMock()
        mock_response.content = "改善案を示します:\n```prompt\nImproved prompt content here\n```\n以上です。"

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await optimize_prompt(
                pipeline="construction/estimation",
                step_name="extract",
                failing_examples=[{"input": "test", "output": "bad", "confidence": 0.5}],
                current_prompt="Current prompt",
            )

        assert result == "Improved prompt content here"

    @pytest.mark.asyncio
    async def test_extracts_generic_code_block(self):
        """```prompt がなく ``` だけの場合も中身を抽出する。"""
        mock_response = MagicMock()
        mock_response.content = "提案:\n```\nGeneric improved prompt\n```"

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await optimize_prompt(
                pipeline="dental/claim",
                step_name="classify",
                failing_examples=[],
                current_prompt="Old prompt",
            )

        assert result == "Generic improved prompt"

    @pytest.mark.asyncio
    async def test_returns_full_content_when_no_code_block(self):
        """コードブロックなしの場合は応答全文を返す。"""
        mock_response = MagicMock()
        mock_response.content = "プロンプトを改善してください。具体的には..."

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await optimize_prompt(
                pipeline="mfg/bom",
                step_name="parse",
                failing_examples=[],
                current_prompt="Old prompt",
            )

        assert result == "プロンプトを改善してください。具体的には..."

    @pytest.mark.asyncio
    async def test_returns_none_on_llm_error(self):
        """LLM呼び出し失敗時は None を返す。"""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("All models failed"))

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await optimize_prompt(
                pipeline="construction/estimation",
                step_name="extract",
                failing_examples=[],
                current_prompt="Current prompt",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_content(self):
        """LLMが空文字を返した場合は None を返す。"""
        mock_response = MagicMock()
        mock_response.content = "   "

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await optimize_prompt(
                pipeline="test/pipe",
                step_name="step1",
                failing_examples=[],
                current_prompt="Current prompt",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_fast_tier(self):
        """ModelTier.FAST で LLM が呼ばれる（Gemini Flash 優先）。"""
        from llm.client import ModelTier

        mock_response = MagicMock()
        mock_response.content = "improved"

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            await optimize_prompt("pipe", "step", [], "prompt")

        call_args = mock_llm.generate.call_args[0][0]
        assert call_args.tier == ModelTier.FAST

    @pytest.mark.asyncio
    async def test_failing_examples_truncated_to_5(self):
        """failing_examples が6件以上あっても最大5件しか使わない。"""
        mock_response = MagicMock()
        mock_response.content = "improved"

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        examples = [{"input": f"input{i}", "output": f"out{i}", "confidence": 0.5} for i in range(10)]

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            await optimize_prompt("pipe", "step", examples, "prompt")

        user_message = mock_llm.generate.call_args[0][0].messages[1]["content"]
        # 事例1〜5 のみ（事例6以降はない）
        assert "【事例5】" in user_message
        assert "【事例6】" not in user_message


# ─────────────────────────────────────
# TestRunImprovementCycle
# ─────────────────────────────────────

class TestRunImprovementCycle:

    def _make_report(self, needs: bool, pipeline: str = "test/pipe", step: str = "step1") -> StepAccuracyReport:
        avg = 0.6 if needs else 0.9
        return StepAccuracyReport(
            pipeline=pipeline,
            step_name=step,
            avg_confidence=avg,
            call_count=10,
            low_confidence_count=5 if needs else 0,
            feedback_negative_count=0,
            needs_improvement=needs,
        )

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_files(self, tmp_path):
        """dry_run=True ではファイルを変更しない。"""
        report = self._make_report(needs=True)

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew improved prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=[report])),
            patch("brain.inference.improvement_cycle._collect_failing_examples", return_value=[]),
            patch("brain.inference.improvement_cycle._read_current_prompt", return_value="old prompt"),
            patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion") as mock_write,
        ):
            result = await run_improvement_cycle(dry_run=True)

        mock_write.assert_not_called()
        assert result["dry_run"] is True
        assert len(result["improved_steps"]) == 1
        assert result["improved_steps"][0]["applied"] is False

    @pytest.mark.asyncio
    async def test_not_dry_run_writes_files(self):
        """dry_run=False のときファイルを書き出す。"""
        report = self._make_report(needs=True)

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew improved prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=[report])),
            patch("brain.inference.improvement_cycle._collect_failing_examples", return_value=[]),
            patch("brain.inference.improvement_cycle._read_current_prompt", return_value="old prompt"),
            patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion") as mock_write,
        ):
            result = await run_improvement_cycle(dry_run=False)

        mock_write.assert_called_once()
        assert result["improved_steps"][0]["applied"] is True

    @pytest.mark.asyncio
    async def test_only_targets_needs_improvement(self):
        """needs_improvement=False のステップは対象外。"""
        reports = [
            self._make_report(needs=False, pipeline="pipe/ok", step="s1"),
            self._make_report(needs=True, pipeline="pipe/bad", step="s2"),
        ]

        mock_response = MagicMock()
        mock_response.content = "New prompt"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=reports)),
            patch("brain.inference.improvement_cycle._collect_failing_examples", return_value=[]),
            patch("brain.inference.improvement_cycle._read_current_prompt", return_value="old"),
            patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion"),
        ):
            result = await run_improvement_cycle(dry_run=True)

        assert result["checked_steps"] == 2
        assert result["improvement_targets"] == 1
        assert result["improved_steps"][0]["pipeline"] == "pipe/bad"

    @pytest.mark.asyncio
    async def test_no_targets_returns_empty_improved(self):
        """全ステップが OK の場合 improved_steps が空。"""
        reports = [self._make_report(needs=False)]

        with patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=reports)):
            result = await run_improvement_cycle(dry_run=True)

        assert result["improvement_targets"] == 0
        assert result["improved_steps"] == []

    @pytest.mark.asyncio
    async def test_result_contains_company_id(self):
        """戻り値に company_id が含まれる。"""
        with patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=[])):
            result = await run_improvement_cycle(company_id=COMPANY_ID, dry_run=True)

        assert result["company_id"] == COMPANY_ID

    @pytest.mark.asyncio
    async def test_no_suggestion_means_not_in_improved(self):
        """optimize_prompt が None を返した場合は improved_steps に含まれない。"""
        report = self._make_report(needs=True)

        with (
            patch("brain.inference.improvement_cycle.get_accuracy_report", AsyncMock(return_value=[report])),
            patch("brain.inference.improvement_cycle._collect_failing_examples", return_value=[]),
            patch("brain.inference.improvement_cycle._read_current_prompt", return_value="old"),
            patch("brain.inference.improvement_cycle.optimize_prompt", AsyncMock(return_value=None)),
        ):
            result = await run_improvement_cycle(dry_run=True)

        assert result["improvement_targets"] == 1
        assert result["improved_steps"] == []


# ─────────────────────────────────────
# TestFeedbackAPI (FastAPI TestClient)
# ─────────────────────────────────────

def _make_api_client(user: JWTClaims) -> TestClient:
    """テスト用 FastAPI + TestClient を生成（認証をモック）。"""
    from auth.middleware import get_current_user, require_role
    app2 = FastAPI()
    app2.include_router(router)
    app2.include_router(execution_router)
    app2.dependency_overrides[get_current_user] = lambda: user
    app2.dependency_overrides[require_role("admin")] = lambda: user
    app2.dependency_overrides[require_role("editor")] = lambda: user
    return TestClient(app2, raise_server_exceptions=False)


class TestFeedbackAPI:

    def _make_db_with_exec(self, exec_id: str, exists: bool = True) -> MagicMock:
        mock_db = MagicMock()
        select_result = MagicMock()
        # maybe_single() の場合、存在しないと data=None を返す（Supabase の仕様）
        select_result.data = {"id": exec_id, "operations": {}, "company_id": COMPANY_ID} if exists else None

        update_result = MagicMock()
        update_result.data = [{"id": exec_id}]

        select_chain = mock_db.table.return_value
        for method in ("select", "eq", "update", "maybe_single"):
            getattr(select_chain, method).return_value = select_chain
        select_chain.execute.return_value = select_result

        return mock_db

    def test_good_rating_saves_successfully(self):
        """overall_approved=True のフィードバックが正常に保存される。"""
        exec_id = str(uuid.uuid4())
        mock_db = self._make_db_with_exec(exec_id, exists=True)

        client = _make_api_client(EDITOR_USER)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            resp = client.post(
                f"/execution/{exec_id}/feedback",
                json={"overall_approved": True, "overall_comment": "great"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_id"] == exec_id
        assert "feedback_id" in data
        assert "learning_triggered" in data

    def test_partial_rating_saves_successfully(self):
        """overall_approved=False のフィードバックが正常に保存される（学習トリガーあり）。"""
        exec_id = str(uuid.uuid4())
        mock_db = self._make_db_with_exec(exec_id, exists=True)

        client = _make_api_client(EDITOR_USER)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            resp = client.post(
                f"/execution/{exec_id}/feedback",
                json={"overall_approved": False},
            )

        assert resp.status_code == 200
        assert resp.json()["learning_triggered"] is True

    def test_bad_rating_saves_successfully(self):
        """overall_approved=False かつコメントあり のフィードバックが正常に保存される。"""
        exec_id = str(uuid.uuid4())
        mock_db = self._make_db_with_exec(exec_id, exists=True)

        client = _make_api_client(EDITOR_USER)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            resp = client.post(
                f"/execution/{exec_id}/feedback",
                json={"overall_approved": False, "overall_comment": "wrong output"},
            )

        assert resp.status_code == 200
        assert resp.json()["learning_triggered"] is True

    def test_invalid_rating_returns_422(self):
        """必須フィールド overall_approved が欠けた場合に 422 を返す。"""
        exec_id = str(uuid.uuid4())
        mock_db = self._make_db_with_exec(exec_id, exists=True)

        client = _make_api_client(EDITOR_USER)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            resp = client.post(
                f"/execution/{exec_id}/feedback",
                json={"overall_comment": "no approved field"},  # overall_approved 欠落
            )

        assert resp.status_code == 422

    def test_nonexistent_execution_returns_404(self):
        """存在しない execution_id で 404 を返す。"""
        exec_id = str(uuid.uuid4())
        mock_db = self._make_db_with_exec(exec_id, exists=False)

        client = _make_api_client(EDITOR_USER)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            resp = client.post(
                f"/execution/{exec_id}/feedback",
                json={"overall_approved": True},
            )

        assert resp.status_code == 404

    def test_accuracy_report_endpoint(self):
        """GET /accuracy/report が reports リストを返す。"""
        mock_reports = [
            StepAccuracyReport(
                pipeline="construction/estimation",
                step_name="extract",
                avg_confidence=0.85,
                call_count=10,
                low_confidence_count=1,
                feedback_negative_count=0,
                needs_improvement=False,
            )
        ]

        client = _make_api_client(EDITOR_USER)
        with patch(
            "routers.accuracy.get_accuracy_report",
            AsyncMock(return_value=mock_reports),
        ):
            resp = client.get("/accuracy/report?days=7")

        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data
        assert data["days"] == 7
        assert len(data["reports"]) == 1
        assert data["reports"][0]["pipeline"] == "construction/estimation"

    def test_improve_endpoint_returns_cycle_result(self):
        """POST /accuracy/improve が cycle 結果を返す。"""
        mock_result = {
            "checked_steps": 5,
            "improvement_targets": 1,
            "improved_steps": [],
            "dry_run": True,
            "company_id": COMPANY_ID,
        }

        client = _make_api_client(ADMIN_USER)
        with patch(
            "routers.accuracy.run_improvement_cycle",
            AsyncMock(return_value=mock_result),
        ):
            resp = client.post("/accuracy/improve", json={"dry_run": True, "days": 7})

        assert resp.status_code == 200
        data = resp.json()
        assert data["checked_steps"] == 5
        assert data["dry_run"] is True

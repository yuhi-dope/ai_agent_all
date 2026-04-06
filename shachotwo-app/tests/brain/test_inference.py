"""Tests for brain/inference module (accuracy monitor, prompt optimizer, improvement cycle, feedback API)."""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from brain.inference.accuracy_monitor import (
    get_accuracy_report,
    check_and_respond_to_degradation,
    run_accuracy_check_pipeline,
    StepAccuracyReport,
    DegradationResponse,
)
from brain.inference.prompt_optimizer import optimize_prompt
from brain.inference.improvement_cycle import (
    run_improvement_cycle,
    _collect_failing_examples,
    _read_current_prompt,
)
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


# ─────────────────────────────────────
# TestRunAccuracyCheckPipeline
# ─────────────────────────────────────

class TestRunAccuracyCheckPipeline:
    """run_accuracy_check_pipeline() のユニットテスト。"""

    @pytest.mark.asyncio
    async def test_success_no_degradation(self):
        """精度劣化なしの場合、success=True かつ declining_pipelines が空のdictを返す。"""
        mock_reports = [
            StepAccuracyReport(
                pipeline="construction/estimation",
                step_name="extract",
                avg_confidence=0.90,
                call_count=10,
                low_confidence_count=0,
                feedback_negative_count=0,
                needs_improvement=False,
            )
        ]
        mock_degradation = DegradationResponse(
            demoted_pipelines=[],
            notified=False,
            skipped_pipelines=[],
        )

        with (
            patch(
                "brain.inference.accuracy_monitor.get_accuracy_report",
                AsyncMock(return_value=mock_reports),
            ),
            patch(
                "brain.inference.accuracy_monitor.check_and_respond_to_degradation",
                AsyncMock(return_value=mock_degradation),
            ),
        ):
            result = await run_accuracy_check_pipeline(company_id=COMPANY_ID)

        assert result["success"] is True
        assert result["declining_pipelines"] == []
        assert result["skipped_pipelines"] == []
        assert result["notified"] is False
        assert result["total_steps_checked"] == 1
        assert result["needs_improvement_count"] == 0

    @pytest.mark.asyncio
    async def test_success_with_declining_pipeline(self):
        """精度劣化ありの場合、declining_pipelines に対象パイプラインが含まれる。"""
        mock_reports = [
            StepAccuracyReport(
                pipeline="construction/estimation",
                step_name="extract",
                avg_confidence=0.60,
                call_count=10,
                low_confidence_count=6,
                feedback_negative_count=2,
                needs_improvement=True,
                trend="declining",
            )
        ]
        mock_degradation = DegradationResponse(
            demoted_pipelines=["construction/estimation"],
            notified=True,
            skipped_pipelines=[],
        )

        with (
            patch(
                "brain.inference.accuracy_monitor.get_accuracy_report",
                AsyncMock(return_value=mock_reports),
            ),
            patch(
                "brain.inference.accuracy_monitor.check_and_respond_to_degradation",
                AsyncMock(return_value=mock_degradation),
            ),
        ):
            result = await run_accuracy_check_pipeline(company_id=COMPANY_ID)

        assert result["success"] is True
        assert result["declining_pipelines"] == ["construction/estimation"]
        assert result["notified"] is True
        assert result["needs_improvement_count"] == 1

    @pytest.mark.asyncio
    async def test_returns_failure_on_exception(self):
        """get_accuracy_report が例外を投げた場合、success=False の dict を返す（例外は伝播しない）。"""
        with patch(
            "brain.inference.accuracy_monitor.get_accuracy_report",
            AsyncMock(side_effect=RuntimeError("DB接続失敗")),
        ):
            result = await run_accuracy_check_pipeline(company_id=COMPANY_ID)

        assert result["success"] is False
        assert "error" in result
        assert result["declining_pipelines"] == []
        assert result["total_steps_checked"] == 0

    @pytest.mark.asyncio
    async def test_passes_company_id_to_get_accuracy_report(self):
        """get_accuracy_report に正しい company_id が渡される。"""
        mock_degradation = DegradationResponse(
            demoted_pipelines=[], notified=False, skipped_pipelines=[]
        )
        mock_get = AsyncMock(return_value=[])
        mock_check = AsyncMock(return_value=mock_degradation)

        with (
            patch("brain.inference.accuracy_monitor.get_accuracy_report", mock_get),
            patch(
                "brain.inference.accuracy_monitor.check_and_respond_to_degradation",
                mock_check,
            ),
        ):
            await run_accuracy_check_pipeline(company_id=COMPANY_ID)

        mock_get.assert_called_once_with(company_id=COMPANY_ID, days=7)
        mock_check.assert_called_once_with(company_id=COMPANY_ID)

    @pytest.mark.asyncio
    async def test_accepts_extra_kwargs(self):
        """task_router が **kwargs を渡しても正常に動作する。"""
        mock_degradation = DegradationResponse(
            demoted_pipelines=[], notified=False, skipped_pipelines=[]
        )

        with (
            patch(
                "brain.inference.accuracy_monitor.get_accuracy_report",
                AsyncMock(return_value=[]),
            ),
            patch(
                "brain.inference.accuracy_monitor.check_and_respond_to_degradation",
                AsyncMock(return_value=mock_degradation),
            ),
        ):
            result = await run_accuracy_check_pipeline(
                company_id=COMPANY_ID,
                input_data={"some_key": "value"},
                builtin=True,
                description="パイプライン精度監視（日次）",
            )

        assert result["success"] is True


# ─────────────────────────────────────
# TestCollectFailingExamples
# ─────────────────────────────────────

class TestCollectFailingExamples:
    """_collect_failing_examples() のユニットテスト。"""

    def _make_db(self, rows: list[dict]) -> MagicMock:
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.data = rows
        chain = mock_db.table.return_value
        for method in ("select", "eq", "order", "limit"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = mock_result
        return mock_db

    def _make_row(
        self,
        pipeline: str,
        steps: list[dict],
        company_id: str = COMPANY_ID,
        created_at: str = "2026-03-20T10:00:00Z",
    ) -> dict:
        return {
            "operations": {"pipeline": pipeline, "steps": steps},
            "company_id": company_id,
            "created_at": created_at,
        }

    def test_returns_matching_steps(self):
        """pipeline と step_name が一致するレコードを返す。"""
        rows = [
            self._make_row(
                "construction/estimation",
                [{"step": "extract", "result": "output1", "error": "エラー1"}],
                created_at="2026-03-20T10:00:00Z",
            )
        ]
        mock_db = self._make_db(rows)

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("construction/estimation", "extract")

        assert len(result) == 1
        assert result[0]["error"] == "エラー1"
        assert result[0]["created_at"] == "2026-03-20T10:00:00Z"

    def test_filters_by_pipeline_name(self):
        """pipeline が異なるレコードは除外される。"""
        rows = [
            self._make_row("dental/claim", [{"step": "extract", "result": "x", "error": "e"}]),
            self._make_row("construction/estimation", [{"step": "extract", "result": "y", "error": "e2"}]),
        ]
        mock_db = self._make_db(rows)

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("construction/estimation", "extract")

        assert len(result) == 1
        assert result[0]["output"] == "y"

    def test_filters_by_step_name(self):
        """step_name が異なるステップは除外される。"""
        rows = [
            self._make_row(
                "construction/estimation",
                [
                    {"step": "ocr", "result": "ocr_result", "error": ""},
                    {"step": "extract", "result": "extract_result", "error": "抽出失敗"},
                ],
            )
        ]
        mock_db = self._make_db(rows)

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("construction/estimation", "extract")

        assert len(result) == 1
        assert result[0]["error"] == "抽出失敗"

    def test_company_id_filter_applied(self):
        """company_id が指定された場合 eq() が呼ばれる。"""
        mock_db = self._make_db([])
        chain = mock_db.table.return_value

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            _collect_failing_examples("pipe/x", "step1", company_id=COMPANY_ID)

        # eq が "company_id" と COMPANY_ID で呼ばれていることを確認
        calls = [str(c) for c in chain.eq.call_args_list]
        assert any(COMPANY_ID in c for c in calls)

    def test_no_company_id_skips_filter(self):
        """company_id=None のとき eq("company_id", ...) は呼ばれない。"""
        mock_db = self._make_db([])
        chain = mock_db.table.return_value

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            _collect_failing_examples("pipe/x", "step1", company_id=None)

        # eq が呼ばれても "overall_success" に対してのみ
        for call in chain.eq.call_args_list:
            assert call.args[0] != "company_id"

    def test_max_10_results(self):
        """返り値は最大10件。"""
        steps = [{"step": "extract", "result": f"out{i}", "error": f"err{i}"} for i in range(5)]
        rows = [self._make_row("pipe/x", steps) for _ in range(4)]  # 4行 × 5ステップ = 20件
        mock_db = self._make_db(rows)

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("pipe/x", "extract")

        assert len(result) <= 10

    def test_returns_empty_on_db_error(self):
        """DB例外時は空リストを返す（例外を握りつぶす）。"""
        mock_db = MagicMock()
        mock_db.table.side_effect = RuntimeError("DB接続失敗")

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("pipe/x", "step1")

        assert result == []

    def test_result_contains_created_at(self):
        """返り値に created_at フィールドが含まれる。"""
        rows = [
            self._make_row(
                "pipe/x",
                [{"step": "s1", "result": "r", "error": "e"}],
                created_at="2026-01-15T08:30:00Z",
            )
        ]
        mock_db = self._make_db(rows)

        with patch("brain.inference.improvement_cycle.get_service_client", return_value=mock_db):
            result = _collect_failing_examples("pipe/x", "s1")

        assert result[0]["created_at"] == "2026-01-15T08:30:00Z"


# ─────────────────────────────────────
# TestReadCurrentPrompt
# ─────────────────────────────────────

class TestReadCurrentPrompt:
    """_read_current_prompt() のユニットテスト。"""

    def test_reads_pipeline_step_combined_file(self, tmp_path):
        """pipeline_safe_step_safe.py が存在すればその内容を返す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "construction_estimation_extract.py").write_text("プロンプト内容A", encoding="utf-8")

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("construction/estimation", "extract")

        assert result == "プロンプト内容A"

    def test_falls_back_to_pipeline_only_file(self, tmp_path):
        """pipeline_step ファイルがなく pipeline.py があればそちらを返す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "construction_estimation.py").write_text("パイプライン共通プロンプト", encoding="utf-8")

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("construction/estimation", "unknown_step")

        assert result == "パイプライン共通プロンプト"

    def test_falls_back_to_step_only_file(self, tmp_path):
        """pipeline ファイルもなく step.py があればそちらを返す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "extract.py").write_text("ステップ共通プロンプト", encoding="utf-8")

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("unknown/pipeline", "extract")

        assert result == "ステップ共通プロンプト"

    def test_returns_empty_when_no_file(self, tmp_path):
        """いずれのファイルも存在しない場合は空文字列を返す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("no/match", "step")

        assert result == ""

    def test_strips_workers_prefix(self, tmp_path):
        """pipeline が 'workers/bpo/construction/estimation' の場合、workers_bpo_ を除去してファイルを探す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "construction_estimation.py").write_text("建設見積プロンプト", encoding="utf-8")

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("workers/bpo/construction/estimation", "step_x")

        assert result == "建設見積プロンプト"

    def test_strips_workers_only_prefix(self, tmp_path):
        """pipeline が 'workers/dental/claim' の場合、workers_ を除去してファイルを探す。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "dental_claim.py").write_text("歯科クレームプロンプト", encoding="utf-8")

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("workers/dental/claim", "step_y")

        assert result == "歯科クレームプロンプト"

    def test_returns_empty_on_read_error(self, tmp_path):
        """ファイル読込例外時は空文字列を返す（例外を握りつぶす）。"""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        target = prompts_dir / "pipe_step.py"
        target.write_text("内容", encoding="utf-8")
        # 読み取り権限を剥奪
        target.chmod(0o000)

        with patch("brain.inference.improvement_cycle._PROMPTS_DIR", str(prompts_dir)):
            result = _read_current_prompt("pipe", "step")

        # 権限を戻す（クリーンアップ）
        target.chmod(0o644)
        assert result == ""


# ─────────────────────────────────────
# TestGetAccuracyReportTrend
# ─────────────────────────────────────

class TestGetAccuracyReportTrend:
    """get_accuracy_report が trend フィールドを正しく付与することをテスト。"""

    def _make_mock_db_with_trend(self, recent_rows: list, prev_rows: list):
        """execute() の呼び出し順で異なるデータを返すモックDB。

        1回目: get_accuracy_report 本体クエリ (recent_rows)
        2回目: _compute_trend の recent クエリ
        3回目: _compute_trend の prev クエリ
        """
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        main_result = MagicMock()
        main_result.data = recent_rows
        trend_recent = MagicMock()
        trend_recent.data = recent_rows
        trend_prev = MagicMock()
        trend_prev.data = prev_rows
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.side_effect = [main_result, trend_recent, trend_prev]
        return mock_db

    @pytest.mark.asyncio
    async def test_trend_declining(self):
        """直近7日 avg が前7日 avg より 0.03 以上低い場合 trend='declining'。"""
        recent_rows = [_make_exec_row("construction/estimation", [
            {"step": "extract", "confidence": 0.65},
        ]) for _ in range(5)]
        prev_rows = [_make_exec_row("construction/estimation", [
            {"step": "extract", "confidence": 0.70},
        ]) for _ in range(5)]
        mock_db = self._make_mock_db_with_trend(recent_rows, prev_rows)
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report(company_id=COMPANY_ID, days=7)
        assert len(reports) == 1
        assert reports[0].trend == "declining"

    @pytest.mark.asyncio
    async def test_trend_improving(self):
        """直近7日 avg が前7日 avg より 0.03 以上高い場合 trend='improving'。"""
        recent_rows = [_make_exec_row("construction/billing", [
            {"step": "validate", "confidence": 0.85},
        ]) for _ in range(5)]
        prev_rows = [_make_exec_row("construction/billing", [
            {"step": "validate", "confidence": 0.75},
        ]) for _ in range(5)]
        mock_db = self._make_mock_db_with_trend(recent_rows, prev_rows)
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report(company_id=COMPANY_ID, days=7)
        assert len(reports) == 1
        assert reports[0].trend == "improving"

    @pytest.mark.asyncio
    async def test_trend_stable(self):
        """差が -0.03 〜 +0.03 の範囲内なら trend='stable'。"""
        recent_rows = [_make_exec_row("manufacturing/qc", [
            {"step": "check", "confidence": 0.80},
        ]) for _ in range(5)]
        prev_rows = [_make_exec_row("manufacturing/qc", [
            {"step": "check", "confidence": 0.81},
        ]) for _ in range(5)]
        mock_db = self._make_mock_db_with_trend(recent_rows, prev_rows)
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report(company_id=COMPANY_ID, days=7)
        assert len(reports) == 1
        assert reports[0].trend == "stable"

    @pytest.mark.asyncio
    async def test_trend_stable_when_no_prev_data(self):
        """前7日データがない場合は trend='stable' にフォールバック。"""
        recent_rows = [_make_exec_row("logistics/routing", [
            {"step": "calc", "confidence": 0.70},
        ]) for _ in range(5)]
        mock_db = self._make_mock_db_with_trend(recent_rows, [])
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report(company_id=COMPANY_ID, days=7)
        assert len(reports) == 1
        assert reports[0].trend == "stable"

    @pytest.mark.asyncio
    async def test_trend_boundary_exactly_minus_003_is_declining(self):
        """diff がちょうど -0.03 の場合は declining になる（境界値テスト）。"""
        recent_rows = [_make_exec_row("realestate/appraisal", [
            {"step": "estimate", "confidence": 0.70},
        ]) for _ in range(5)]
        prev_rows = [_make_exec_row("realestate/appraisal", [
            {"step": "estimate", "confidence": 0.73},
        ]) for _ in range(5)]
        mock_db = self._make_mock_db_with_trend(recent_rows, prev_rows)
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db):
            reports = await get_accuracy_report(company_id=COMPANY_ID, days=7)
        assert reports[0].trend == "declining"


# ─────────────────────────────────────
# TestCheckAndRespondToDegradation
# ─────────────────────────────────────

class TestCheckAndRespondToDegradation:
    """check_and_respond_to_degradation の動作テスト。"""

    def _declining_report(self, pipeline: str, step: str = "extract") -> StepAccuracyReport:
        return StepAccuracyReport(
            pipeline=pipeline,
            step_name=step,
            avg_confidence=0.65,
            call_count=10,
            low_confidence_count=7,
            feedback_negative_count=2,
            needs_improvement=True,
            trend="declining",
        )

    def _stable_report(self, pipeline: str) -> StepAccuracyReport:
        return StepAccuracyReport(
            pipeline=pipeline,
            step_name="validate",
            avg_confidence=0.85,
            call_count=10,
            low_confidence_count=1,
            feedback_negative_count=0,
            needs_improvement=False,
            trend="stable",
        )

    @pytest.mark.asyncio
    async def test_no_degradation_returns_empty(self):
        """declining ステップがない場合は空の DegradationResponse を返す。"""
        with patch(
            "brain.inference.accuracy_monitor.get_accuracy_report",
            AsyncMock(return_value=[self._stable_report("construction/estimation")]),
        ):
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert result.demoted_pipelines == []
        assert result.notified is False
        assert result.skipped_pipelines == []

    @pytest.mark.asyncio
    async def test_declining_pipeline_gets_demoted_and_notified(self):
        """declining+needs_improvement のパイプラインが降格され通知される。"""
        report = self._declining_report("construction/estimation")
        hitl_result = MagicMock()
        hitl_result.data = [{"id": str(uuid.uuid4()), "min_confidence_for_auto": 0.85}]
        update_result = MagicMock()
        update_result.data = []
        mock_db = MagicMock()
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.side_effect = [hitl_result, update_result]
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db), \
             patch(
                 "brain.inference.accuracy_monitor.get_accuracy_report",
                 AsyncMock(return_value=[report]),
             ), \
             patch(
                 "workers.bpo.manager.notifier.notify_pipeline_event",
                 AsyncMock(return_value=True),
             ):
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert "construction/estimation" in result.demoted_pipelines
        assert result.notified is True
        assert result.skipped_pipelines == []
        update_calls = chain.update.call_args_list
        assert len(update_calls) >= 1
        updated_value = update_calls[0][0][0]["min_confidence_for_auto"]
        assert abs(updated_value - 0.90) < 1e-9

    @pytest.mark.asyncio
    async def test_pipeline_already_at_max_confidence_is_skipped(self):
        """min_confidence_for_auto=0.96 → bump後 1.01 → 上限判定でスキップ。"""
        report = self._declining_report("manufacturing/procurement")
        hitl_result = MagicMock()
        hitl_result.data = [{"id": str(uuid.uuid4()), "min_confidence_for_auto": 0.96}]
        mock_db = MagicMock()
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = hitl_result
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db), \
             patch(
                 "brain.inference.accuracy_monitor.get_accuracy_report",
                 AsyncMock(return_value=[report]),
             ), \
             patch(
                 "workers.bpo.manager.notifier.notify_pipeline_event",
                 AsyncMock(return_value=True),
             ):
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert "manufacturing/procurement" in result.skipped_pipelines
        assert result.demoted_pipelines == []
        chain.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_with_null_confidence_is_skipped(self):
        """min_confidence_for_auto=NULL (常にHitL) のパイプラインは降格スキップ。"""
        report = self._declining_report("construction/billing")
        hitl_result = MagicMock()
        hitl_result.data = [{"id": str(uuid.uuid4()), "min_confidence_for_auto": None}]
        mock_db = MagicMock()
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = hitl_result
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db), \
             patch(
                 "brain.inference.accuracy_monitor.get_accuracy_report",
                 AsyncMock(return_value=[report]),
             ), \
             patch(
                 "workers.bpo.manager.notifier.notify_pipeline_event",
                 AsyncMock(return_value=True),
             ):
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert "construction/billing" in result.skipped_pipelines
        assert result.demoted_pipelines == []

    @pytest.mark.asyncio
    async def test_new_pipeline_entry_created_when_not_in_db(self):
        """bpo_hitl_requirements にエントリがない場合は新規作成 (min_confidence=0.95)。"""
        report = self._declining_report("wholesale/order")
        empty_result = MagicMock()
        empty_result.data = []
        insert_result = MagicMock()
        insert_result.data = []
        mock_db = MagicMock()
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.side_effect = [empty_result, insert_result]
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db), \
             patch(
                 "brain.inference.accuracy_monitor.get_accuracy_report",
                 AsyncMock(return_value=[report]),
             ), \
             patch(
                 "workers.bpo.manager.notifier.notify_pipeline_event",
                 AsyncMock(return_value=True),
             ):
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert "wholesale/order" in result.demoted_pipelines
        chain.insert.assert_called_once()
        insert_args = chain.insert.call_args[0][0]
        assert insert_args["pipeline_key"] == "wholesale/order"
        assert insert_args["min_confidence_for_auto"] == 0.95
        assert insert_args["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_multiple_steps_same_pipeline_demoted_once(self):
        """同一パイプラインの複数ステップが declining でも降格・通知は1回だけ。"""
        reports = [
            StepAccuracyReport(
                pipeline="construction/estimation",
                step_name="extract",
                avg_confidence=0.65,
                call_count=10,
                low_confidence_count=7,
                feedback_negative_count=2,
                needs_improvement=True,
                trend="declining",
            ),
            StepAccuracyReport(
                pipeline="construction/estimation",
                step_name="validate",
                avg_confidence=0.60,
                call_count=8,
                low_confidence_count=6,
                feedback_negative_count=1,
                needs_improvement=True,
                trend="declining",
            ),
        ]
        hitl_result = MagicMock()
        hitl_result.data = [{"id": str(uuid.uuid4()), "min_confidence_for_auto": 0.85}]
        update_result = MagicMock()
        update_result.data = []
        mock_db = MagicMock()
        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "lte", "lt", "update", "insert", "neq"):
            getattr(chain, method).return_value = chain
        chain.execute.side_effect = [hitl_result, update_result]
        with patch("brain.inference.accuracy_monitor.get_service_client", return_value=mock_db), \
             patch(
                 "brain.inference.accuracy_monitor.get_accuracy_report",
                 AsyncMock(return_value=reports),
             ), \
             patch(
                 "workers.bpo.manager.notifier.notify_pipeline_event",
                 AsyncMock(return_value=True),
             ) as mock_notify:
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert result.demoted_pipelines == ["construction/estimation"]
        assert chain.update.call_count == 1
        assert mock_notify.call_count == 1

    @pytest.mark.asyncio
    async def test_needs_improvement_without_declining_not_demoted(self):
        """needs_improvement=True でも trend が stable なら降格・通知しない。"""
        report = StepAccuracyReport(
            pipeline="dental/claim",
            step_name="classify",
            avg_confidence=0.70,
            call_count=8,
            low_confidence_count=5,
            feedback_negative_count=0,
            needs_improvement=True,
            trend="stable",
        )
        with patch(
            "brain.inference.accuracy_monitor.get_accuracy_report",
            AsyncMock(return_value=[report]),
        ), patch(
            "workers.bpo.manager.notifier.notify_pipeline_event",
            AsyncMock(return_value=True),
        ) as mock_notify:
            result = await check_and_respond_to_degradation(COMPANY_ID)
        assert result.demoted_pipelines == []
        assert result.notified is False
        mock_notify.assert_not_called()


# ─────────────────────────────────────
# TestRunImprovementCycleNewFeatures (修正仕様のテスト)
# ─────────────────────────────────────

class TestRunImprovementCycleNewFeatures:
    """修正後の run_improvement_cycle: 30日ウィンドウ + 改善済みスキップテスト。"""

    def _make_report(
        self,
        pipeline: str = "test/pipe",
        step: str = "step1",
        needs: bool = True,
    ) -> StepAccuracyReport:
        return StepAccuracyReport(
            pipeline=pipeline,
            step_name=step,
            avg_confidence=0.6 if needs else 0.9,
            call_count=10,
            low_confidence_count=5 if needs else 0,
            feedback_negative_count=0,
            needs_improvement=needs,
        )

    @pytest.mark.asyncio
    async def test_days_capped_at_30(self):
        """days=60 を渡しても get_accuracy_report には min(60, 30)=30 が渡される。"""
        mock_get_report = AsyncMock(return_value=[])

        with patch(
            "brain.inference.improvement_cycle.get_accuracy_report",
            mock_get_report,
        ):
            await run_improvement_cycle(company_id=COMPANY_ID, dry_run=True, days=60)

        # get_accuracy_report の days 引数が 30 に上限クランプされていること
        call_kwargs = mock_get_report.call_args.kwargs
        assert call_kwargs.get("days", None) == 30

    @pytest.mark.asyncio
    async def test_days_30_passed_directly(self):
        """days=30 を渡した場合は 30 がそのまま渡される。"""
        mock_get_report = AsyncMock(return_value=[])

        with patch(
            "brain.inference.improvement_cycle.get_accuracy_report",
            mock_get_report,
        ):
            await run_improvement_cycle(company_id=COMPANY_ID, dry_run=True, days=30)

        call_kwargs = mock_get_report.call_args.kwargs
        assert call_kwargs.get("days", None) == 30

    @pytest.mark.asyncio
    async def test_recently_improved_step_is_skipped(self):
        """improvement_applied_at が7日以内のステップは skip_reason='recently_improved' でスキップ。"""
        from datetime import timedelta
        report = self._make_report()

        # 3日前に改善済みと見なす
        recent_applied_at = datetime.now(timezone.utc) - timedelta(days=3)

        with (
            patch(
                "brain.inference.improvement_cycle.get_accuracy_report",
                AsyncMock(return_value=[report]),
            ),
            patch(
                "brain.inference.improvement_cycle._get_last_improvement_applied_at",
                return_value=recent_applied_at,
            ),
            patch(
                "brain.inference.improvement_cycle._record_skip_reason",
            ) as mock_record_skip,
        ):
            result = await run_improvement_cycle(
                company_id=COMPANY_ID, dry_run=True
            )

        assert result["improved_steps"] == []
        assert len(result["skipped_steps"]) == 1
        skipped = result["skipped_steps"][0]
        assert skipped["skip_reason"] == "recently_improved"
        assert skipped["pipeline"] == "test/pipe"
        mock_record_skip.assert_called_once()

    @pytest.mark.asyncio
    async def test_old_improvement_is_not_skipped(self):
        """improvement_applied_at が8日以上前のステップはスキップされない。"""
        from datetime import timedelta
        report = self._make_report()

        # 8日前に改善済み → スキップ対象外
        old_applied_at = datetime.now(timezone.utc) - timedelta(days=8)

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch(
                "brain.inference.improvement_cycle.get_accuracy_report",
                AsyncMock(return_value=[report]),
            ),
            patch(
                "brain.inference.improvement_cycle._get_last_improvement_applied_at",
                return_value=old_applied_at,
            ),
            patch(
                "brain.inference.improvement_cycle._collect_failing_examples",
                return_value=[],
            ),
            patch(
                "brain.inference.improvement_cycle._read_current_prompt",
                return_value="old prompt",
            ),
            patch(
                "brain.inference.prompt_optimizer.get_llm_client",
                return_value=mock_llm,
            ),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion"),
        ):
            result = await run_improvement_cycle(
                company_id=COMPANY_ID, dry_run=True
            )

        # スキップされずに improved_steps に含まれる
        assert len(result["improved_steps"]) == 1
        assert result["skipped_steps"] == []

    @pytest.mark.asyncio
    async def test_no_previous_improvement_is_not_skipped(self):
        """improvement_applied_at が None（初回）のステップはスキップされない。"""
        report = self._make_report()

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch(
                "brain.inference.improvement_cycle.get_accuracy_report",
                AsyncMock(return_value=[report]),
            ),
            patch(
                "brain.inference.improvement_cycle._get_last_improvement_applied_at",
                return_value=None,  # 初回 → スキップしない
            ),
            patch(
                "brain.inference.improvement_cycle._collect_failing_examples",
                return_value=[],
            ),
            patch(
                "brain.inference.improvement_cycle._read_current_prompt",
                return_value="old prompt",
            ),
            patch(
                "brain.inference.prompt_optimizer.get_llm_client",
                return_value=mock_llm,
            ),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion"),
        ):
            result = await run_improvement_cycle(
                company_id=COMPANY_ID, dry_run=True
            )

        assert len(result["improved_steps"]) == 1
        assert result["skipped_steps"] == []

    @pytest.mark.asyncio
    async def test_improvement_applied_at_updated_after_apply(self):
        """dry_run=False で改善適用後、_update_improvement_applied_at が呼ばれる。"""
        report = self._make_report()

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with (
            patch(
                "brain.inference.improvement_cycle.get_accuracy_report",
                AsyncMock(return_value=[report]),
            ),
            patch(
                "brain.inference.improvement_cycle._get_last_improvement_applied_at",
                return_value=None,
            ),
            patch(
                "brain.inference.improvement_cycle._collect_failing_examples",
                return_value=[],
            ),
            patch(
                "brain.inference.improvement_cycle._read_current_prompt",
                return_value="old prompt",
            ),
            patch(
                "brain.inference.prompt_optimizer.get_llm_client",
                return_value=mock_llm,
            ),
            patch("brain.inference.improvement_cycle._write_prompt_suggestion"),
            patch(
                "brain.inference.improvement_cycle.save_prompt_version",
                AsyncMock(return_value="version-uuid"),
            ),
            patch(
                "brain.inference.improvement_cycle._update_improvement_applied_at",
            ) as mock_update,
        ):
            result = await run_improvement_cycle(
                company_id=COMPANY_ID, dry_run=False
            )

        assert result["improved_steps"][0]["applied"] is True
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args.kwargs
        assert call_kwargs.get("pipeline") == "test/pipe"
        assert call_kwargs.get("step_name") == "step1"

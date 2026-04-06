"""Tests for feedback endpoint (routers/execution.py) and accuracy API (routers/accuracy.py)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.execution import router as execution_router
from routers.accuracy import router as accuracy_router


# ─────────────────────────────────────
# テスト用アプリ
# ─────────────────────────────────────

app = FastAPI()
app.include_router(execution_router)
app.include_router(accuracy_router)


# ─────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
EXECUTION_ID = str(uuid.uuid4())

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


def _make_mock_db(execution_row: dict | None = None, knowledge_rows: list[dict] | None = None):
    """execution_logs / knowledge_items の両テーブルをモックする DB クライアントを返す。"""
    mock_db = MagicMock()

    def table_side_effect(table_name: str):
        chain = MagicMock()
        for method in ("select", "eq", "order", "range", "insert", "update",
                       "maybe_single", "limit", "gte"):
            getattr(chain, method).return_value = chain

        if table_name == "execution_logs":
            result = MagicMock()
            result.data = execution_row
            chain.execute.return_value = result
        elif table_name == "knowledge_items":
            result = MagicMock()
            result.data = knowledge_rows or []
            chain.execute.return_value = result
        elif table_name == "audit_logs":
            result = MagicMock()
            result.data = []
            chain.execute.return_value = result
        else:
            result = MagicMock()
            result.data = []
            result.count = 0
            chain.execute.return_value = result

        return chain

    mock_db.table.side_effect = table_side_effect
    return mock_db


def _make_execution_row(ops_override: dict | None = None) -> dict:
    ops = {
        "pipeline": "manufacturing/quoting",
        "steps": [{"step": "estimate", "confidence": 0.82}],
        "final_output": {"message": "製造業見積書を作成しました"},
    }
    if ops_override:
        ops.update(ops_override)
    return {
        "id": EXECUTION_ID,
        "company_id": COMPANY_ID,
        "operations": ops,
        "overall_success": True,
    }


# ─────────────────────────────────────
# フィードバック API テスト
# ─────────────────────────────────────

class TestSubmitExecutionFeedback:
    def _make_client(self, user: JWTClaims) -> TestClient:
        from auth.middleware import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def test_approved_feedback_returns_200(self):
        """overall_approved=True のフィードバックが 200 を返す。"""
        mock_db = _make_mock_db(execution_row=_make_execution_row())

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{EXECUTION_ID}/feedback",
                json={
                    "overall_approved": True,
                    "overall_comment": "問題ありません",
                    "step_feedbacks": [],
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_id"] == EXECUTION_ID
        assert "feedback_id" in body
        assert body["learning_triggered"] is False  # 承認時は学習不要

    def test_rejected_feedback_triggers_learning(self):
        """overall_approved=False のとき learning_triggered=True を返す。"""
        mock_db = _make_mock_db(execution_row=_make_execution_row())

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution.asyncio.ensure_future") as mock_ensure_future, \
             patch(
                 "brain.inference.improvement_cycle.record_negative_feedback",
                 new_callable=AsyncMock,
             ):
            mock_ensure_future.return_value = None
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{EXECUTION_ID}/feedback",
                json={
                    "overall_approved": False,
                    "overall_comment": "見積金額が大きく外れています",
                    "step_feedbacks": [
                        {"step_no": 0, "approved": False, "comment": "単価が2倍以上"}
                    ],
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["learning_triggered"] is True

    def test_execution_not_found_returns_404(self):
        """存在しない execution_id には 404 を返す。"""
        mock_db = _make_mock_db(execution_row=None)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{uuid.uuid4()}/feedback",
                json={"overall_approved": True},
            )

        assert resp.status_code == 404

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す。"""
        app.dependency_overrides.clear()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            f"/execution/{EXECUTION_ID}/feedback",
            json={"overall_approved": True},
        )
        assert resp.status_code == 401

    def test_step_feedbacks_are_stored(self):
        """ステップフィードバックが operations に保存される。"""
        captured_ops: list[dict] = []
        mock_db = _make_mock_db(execution_row=_make_execution_row())

        # update 呼び出し時の引数をキャプチャ
        original_table = mock_db.table.side_effect

        def table_with_capture(table_name: str):
            chain = original_table(table_name)
            if table_name == "execution_logs":
                def capture_update(data):
                    captured_ops.append(data)
                    return chain
                chain.update.side_effect = capture_update
            return chain

        mock_db.table.side_effect = table_with_capture

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock):
            client = self._make_client(ADMIN_USER)
            client.post(
                f"/execution/{EXECUTION_ID}/feedback",
                json={
                    "overall_approved": True,
                    "overall_comment": "OK",
                    "step_feedbacks": [
                        {"step_no": 0, "approved": True, "comment": "正確"}
                    ],
                },
            )

        # update が呼ばれ、operations に feedback_detail が含まれることを確認
        assert len(captured_ops) >= 1
        updated = captured_ops[0]
        ops = updated.get("operations", {})
        assert "feedback_detail" in ops
        assert ops["feedback_detail"]["overall_approved"] is True
        assert len(ops["feedback_detail"]["step_feedbacks"]) == 1


# ─────────────────────────────────────
# 精度 API テスト
# ─────────────────────────────────────

class TestGetPipelineAccuracies:
    def _make_client(self, user: JWTClaims) -> TestClient:
        from auth.middleware import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def _make_exec_rows(self, pipeline: str, confidences: list[float],
                        created_at: str = "2026-03-28T10:00:00+00:00") -> list[dict]:
        rows = []
        for conf in confidences:
            rows.append({
                "operations": {
                    "pipeline": pipeline,
                    "steps": [{"step": "main", "confidence": conf}],
                    "final_output": {"message": "完了"},
                    "feedback_detail": {"overall_approved": True},
                },
                "overall_success": True,
                "created_at": created_at,
            })
        return rows

    def test_returns_list_of_pipeline_accuracy(self):
        """execution_logs があれば PipelineAccuracy リストを返す。"""
        rows = self._make_exec_rows("manufacturing/quoting", [0.85, 0.90, 0.80])
        mock_db = _make_mock_db(knowledge_rows=[])

        # execution_logs テーブル用に別のモックを設定
        exec_result = MagicMock()
        exec_result.data = rows

        ki_result = MagicMock()
        ki_result.data = []

        def table_side_effect(name):
            chain = MagicMock()
            for m in ("select", "eq", "order", "limit", "gte", "maybe_single"):
                getattr(chain, m).return_value = chain
            if name == "execution_logs":
                chain.execute.return_value = exec_result
            else:
                chain.execute.return_value = ki_result
            return chain

        mock_db.table.side_effect = table_side_effect

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/accuracy/pipelines")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        item = body[0]
        assert "pipeline_name" in item
        assert "confidence" in item
        assert "data_completeness" in item
        assert "accuracy_trend" in item
        assert "recommendations" in item
        assert isinstance(item["recommendations"], list)

    def test_empty_logs_returns_empty_list(self):
        """execution_logs が空なら空リストを返す。"""
        exec_result = MagicMock()
        exec_result.data = []
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit", "gte"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = exec_result
        mock_db.table.return_value = chain

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(EDITOR_USER)
            resp = client.get("/accuracy/pipelines")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す。"""
        app.dependency_overrides.clear()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/accuracy/pipelines")
        assert resp.status_code == 401

    def test_confidence_is_average_of_steps(self):
        """confidence が直近ステップの平均値で計算される。"""
        rows = [
            {
                "operations": {
                    "pipeline": "construction/estimation",
                    "steps": [
                        {"step": "extract", "confidence": 0.6},
                        {"step": "price", "confidence": 0.8},
                    ],
                    "final_output": {},
                },
                "overall_success": True,
                "created_at": "2026-03-28T10:00:00+00:00",
            }
        ]
        exec_result = MagicMock()
        exec_result.data = rows
        ki_result = MagicMock()
        ki_result.data = []

        def table_side_effect(name):
            chain = MagicMock()
            for m in ("select", "eq", "order", "limit", "gte"):
                getattr(chain, m).return_value = chain
            chain.execute.return_value = exec_result if name == "execution_logs" else ki_result
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = table_side_effect

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/accuracy/pipelines")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        # steps の平均 (0.6+0.8)/2 = 0.7
        assert abs(items[0]["confidence"] - 0.7) < 0.01

    def test_accuracy_trend_improving_when_recent_higher(self):
        """直近7日の confidence が前7日より高い場合は improving トレンドになる。"""
        from datetime import datetime, timezone

        # datetime.now() を固定してカットオフを安定させる
        fixed_now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        # recent_cutoff = 2026-03-29 12:00 / prev_cutoff = 2026-03-22 12:00
        recent_date = "2026-04-02T10:00:00+00:00"   # 直近7日内（3日前）
        prev_date   = "2026-03-26T10:00:00+00:00"   # 前7日内（10日前）

        rows = [
            {
                "operations": {
                    "pipeline": "logistics/dispatch",
                    "steps": [{"step": "route", "confidence": 0.95}],
                    "final_output": {},
                },
                "overall_success": True,
                "created_at": recent_date,
            },
            {
                "operations": {
                    "pipeline": "logistics/dispatch",
                    "steps": [{"step": "route", "confidence": 0.70}],
                    "final_output": {},
                },
                "overall_success": True,
                "created_at": prev_date,
            },
        ]
        exec_result = MagicMock()
        exec_result.data = rows
        ki_result = MagicMock()
        ki_result.data = []

        def table_side_effect(name):
            chain = MagicMock()
            for m in ("select", "eq", "order", "limit", "gte"):
                getattr(chain, m).return_value = chain
            chain.execute.return_value = exec_result if name == "execution_logs" else ki_result
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = table_side_effect

        mock_now = MagicMock(return_value=fixed_now)
        with patch("routers.accuracy.get_service_client", return_value=mock_db), \
             patch("routers.accuracy.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat.side_effect = datetime.fromisoformat
            client = self._make_client(ADMIN_USER)
            resp = client.get("/accuracy/pipelines")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["accuracy_trend"] == "improving"


class TestGetDataCompleteness:
    def _make_client(self, user: JWTClaims) -> TestClient:
        from auth.middleware import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    def test_returns_list_of_completeness_details(self):
        """カテゴリ別の DataCompletenessDetail リストを返す。"""
        ki_result = MagicMock()
        ki_result.data = [
            {"category": "製品"},
            {"category": "製品"},
            {"category": "設備"},
        ]
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = ki_result
        mock_db.table.return_value = chain

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(EDITOR_USER)
            resp = client.get("/accuracy/data-completeness")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) > 0
        item = body[0]
        assert "category" in item
        assert "current_count" in item
        assert "recommended_count" in item
        assert "completeness" in item

    def test_completeness_sorted_ascending(self):
        """充足度の低いカテゴリが先頭に来る（昇順ソート）。"""
        ki_result = MagicMock()
        ki_result.data = []  # 全カテゴリ 0 件
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = ki_result
        mock_db.table.return_value = chain

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/accuracy/data-completeness")

        assert resp.status_code == 200
        body = resp.json()
        completeness_values = [item["completeness"] for item in body]
        assert completeness_values == sorted(completeness_values)

    def test_completeness_capped_at_1(self):
        """current_count が recommended_count を超えても completeness は 1.0 を上回らない。"""
        # 製品カテゴリに 100 件（推奨 10 件）
        ki_result = MagicMock()
        ki_result.data = [{"category": "製品"}] * 100
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = ki_result
        mock_db.table.return_value = chain

        with patch("routers.accuracy.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/accuracy/data-completeness")

        assert resp.status_code == 200
        body = resp.json()
        for item in body:
            assert item["completeness"] <= 1.0

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す。"""
        app.dependency_overrides.clear()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/accuracy/data-completeness")
        assert resp.status_code == 401

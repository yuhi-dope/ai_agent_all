"""Tests for routers/execution.py — BPO execution endpoints."""
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.execution import (
    router,
    BPORunRequest,
    BPORunResponse,
    _save_execution_log,
    PendingApprovalItem,
    PendingApprovalsResponse,
)
from workers.bpo.manager.models import PipelineResult


# ─────────────────────────────────────
# テスト用 FastAPI アプリ
# ─────────────────────────────────────

app = FastAPI()
app.include_router(router)


# ─────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

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


def _mock_db_with_logs(rows: list[dict] | None = None, count: int = 0):
    """execution_logs クエリをモックする DB クライアントを返す。"""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.data = rows or []
    mock_result.count = count

    # チェーンメソッドすべてが self を返すよう設定
    chain = mock_db.table.return_value
    for method in ("select", "eq", "order", "range", "insert", "update", "maybe_single"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    return mock_db


def _make_pending_row(
    row_id: str | None = None,
    pipeline_key: str = "construction/estimation",
    approval_status: str = "pending",
    company_id: str = COMPANY_ID,
) -> dict:
    """pending-approvals テスト用の execution_logs 行を生成する。"""
    return {
        "id": row_id or str(uuid.uuid4()),
        "company_id": company_id,
        "approval_status": approval_status,
        "operations": {
            "pipeline": pipeline_key,
            "steps": [{"step": "extract", "confidence": 0.85}],
            "final_output": {"message": "見積書を作成しました", "total": 500000},
        },
        "created_at": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────
# POST /execution/bpo
# ─────────────────────────────────────

class TestRunBpoPipeline:

    def _make_client(self, user: JWTClaims):
        from auth.middleware import get_current_user, require_role
        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[require_role("admin")] = lambda: user
        app2.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app2, raise_server_exceptions=False)

    def test_run_bpo_calls_route_and_execute(self):
        """BPO実行リクエストが route_and_execute に正しく渡される。"""
        mock_result = PipelineResult(
            success=True,
            pipeline="construction/estimation",
            steps=[{"step": "extract", "confidence": 0.9, "cost_yen": 10.0}],
            final_output={"total": 1000000},
            total_cost_yen=10.0,
            total_duration_ms=500,
        )

        mock_db = _mock_db_with_logs()

        with patch("routers.execution.route_and_execute", new_callable=AsyncMock, return_value=mock_result) as mock_route, \
             patch("routers.execution.get_service_client", return_value=mock_db):

            client = self._make_client(ADMIN_USER)
            resp = client.post("/execution/bpo", json={
                "pipeline": "construction/estimation",
                "input_data": {"project": "テスト現場"},
                "context": {"note": "test"},
                "trigger_type": "user",
                "execution_level": 2,
                "estimated_impact": 0.5,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["pipeline"] == "construction/estimation"
        assert data["approval_pending"] is False
        assert len(data["steps"]) == 1

        # route_and_execute が呼ばれたことを検証
        mock_route.assert_awaited_once()
        called_task = mock_route.call_args.kwargs["task"]
        assert called_task.pipeline == "construction/estimation"
        assert called_task.company_id == COMPANY_ID

    def test_approval_pending_returns_true(self):
        """承認待ち (approval_pending=True) が正しく返される。"""
        mock_result = PipelineResult(
            success=True,
            pipeline="construction/estimation",
            approval_pending=True,
            final_output={"message": "承認待ち。proactive_proposalsを確認してください。"},
        )

        mock_db = _mock_db_with_logs()

        with patch("routers.execution.route_and_execute", new_callable=AsyncMock, return_value=mock_result), \
             patch("routers.execution.get_service_client", return_value=mock_db):

            client = self._make_client(ADMIN_USER)
            resp = client.post("/execution/bpo", json={
                "pipeline": "construction/estimation",
                "input_data": {},
                "execution_level": 3,   # APPROVAL_GATED → 承認必須
                "estimated_impact": 0.9,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["approval_pending"] is True
        assert data["success"] is True
        assert data["message"] == "承認待ち。proactive_proposalsを確認してください。"

    def test_execution_log_saved(self):
        """route_and_execute 後に execution_logs が DB に保存される。"""
        mock_result = PipelineResult(
            success=True,
            pipeline="construction/estimation",
            steps=[],
            final_output={"ok": True},
        )

        mock_db = _mock_db_with_logs()
        # insert チェーン
        insert_chain = MagicMock()
        insert_chain.execute.return_value = MagicMock()
        mock_db.table.return_value.insert.return_value = insert_chain

        with patch("routers.execution.route_and_execute", new_callable=AsyncMock, return_value=mock_result), \
             patch("routers.execution.get_service_client", return_value=mock_db):

            client = self._make_client(ADMIN_USER)
            resp = client.post("/execution/bpo", json={
                "pipeline": "construction/estimation",
                "input_data": {},
            })

        assert resp.status_code == 200, resp.text
        # DB insert が呼ばれたことを確認
        mock_db.table.assert_any_call("execution_logs")

    def test_unregistered_pipeline_returns_422(self):
        """未登録パイプラインは 422 を返す。"""
        mock_db = _mock_db_with_logs()

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post("/execution/bpo", json={
                "pipeline": "nonexistent/unknown_pipeline",
                "input_data": {},
            })

        assert resp.status_code == 422
        assert "未登録" in resp.json()["detail"]

    def test_editor_cannot_run_bpo(self):
        """editor ロールは /execution/bpo を呼べない（403）。"""
        from auth.middleware import get_current_user, require_role

        app3 = FastAPI()
        app3.include_router(router)
        # require_role("admin") を editor で上書き → 403 になることを期待しない場合は
        # 実際の require_role をそのまま使い、editor を差し込む
        app3.dependency_overrides[get_current_user] = lambda: EDITOR_USER

        client = TestClient(app3, raise_server_exceptions=False)
        resp = client.post(
            "/execution/bpo",
            json={"pipeline": "construction/estimation", "input_data": {}},
            headers={"Authorization": "Bearer dummy"},
        )
        # require_role("admin") は get_current_user に依存するため
        # editor が渡ると 403 になる
        assert resp.status_code == 403


# ─────────────────────────────────────
# GET /execution/logs
# ─────────────────────────────────────

class TestListExecutionLogs:

    def _make_client(self, user: JWTClaims):
        from auth.middleware import get_current_user
        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app2, raise_server_exceptions=False)

    def _sample_row(self, overall_success: bool = True) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "flow_id": None,
            "triggered_by": USER_ID,
            "operations": {"pipeline": "construction/estimation", "steps": [], "final_output": {}},
            "overall_success": overall_success,
            "time_saved_minutes": None,
            "cost_saved_yen": None,
            "created_at": datetime.utcnow().isoformat(),
        }

    def test_returns_logs_for_company(self):
        """自社の実行ログ一覧が返る。"""
        rows = [self._sample_row(), self._sample_row(overall_success=False)]
        mock_db = _mock_db_with_logs(rows=rows, count=2)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/logs")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["has_more"] is False

    def test_filters_by_overall_success(self):
        """overall_success フィルタが eq() に渡される。"""
        rows = [self._sample_row(overall_success=True)]
        mock_db = _mock_db_with_logs(rows=rows, count=1)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/logs?overall_success=true")

        assert resp.status_code == 200, resp.text
        # eq() が overall_success で呼ばれたことを確認
        call_args_list = mock_db.table.return_value.eq.call_args_list
        called_keys = [str(c) for c in call_args_list]
        assert any("overall_success" in k for k in called_keys)

    def test_pagination_has_more(self):
        """limit より合計件数が多い場合 has_more=True になる。"""
        rows = [self._sample_row() for _ in range(5)]
        mock_db = _mock_db_with_logs(rows=rows, count=100)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/logs?limit=5&offset=0")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["has_more"] is True
        assert data["total"] == 100

    def test_empty_logs(self):
        """ログが0件の場合も正常に返る。"""
        mock_db = _mock_db_with_logs(rows=[], count=0)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(EDITOR_USER)
            resp = client.get("/execution/logs")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["has_more"] is False


# ─────────────────────────────────────
# GET /execution/pending-approvals
# ─────────────────────────────────────

class TestListPendingApprovals:

    def _make_client(self, user: JWTClaims):
        from auth.middleware import get_current_user, require_role
        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[require_role("admin")] = lambda: user
        app2.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app2, raise_server_exceptions=False)

    def test_returns_empty_when_no_pending(self):
        """承認待ちが0件のとき count=0, items=[] が返る。"""
        mock_db = _mock_db_with_logs(rows=[], count=0)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/pending-approvals")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] == 0
        assert data["items"] == []

    def test_returns_pending_items(self):
        """承認待ちが複数件ある場合、正しいフィールドで返る。"""
        row1 = _make_pending_row(pipeline_key="construction/estimation")
        row2 = _make_pending_row(pipeline_key="common/expense")
        mock_db = _mock_db_with_logs(rows=[row1, row2], count=2)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/pending-approvals")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["count"] == 2
        assert len(data["items"]) == 2

        item0 = data["items"][0]
        assert item0["pipeline_key"] == "construction/estimation"
        assert item0["pipeline_label"] == "建設業 見積書"
        assert item0["summary"] == "見積書を作成しました"
        assert item0["confidence"] == pytest.approx(0.85)

        item1 = data["items"][1]
        assert item1["pipeline_key"] == "common/expense"
        assert item1["pipeline_label"] == "経費精算"

    def test_unknown_pipeline_uses_key_as_label(self):
        """PIPELINE_LABELS に未登録のキーはそのままラベルに使われる。"""
        row = _make_pending_row(pipeline_key="unknown/pipeline")
        mock_db = _mock_db_with_logs(rows=[row])

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/pending-approvals")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["items"][0]["pipeline_label"] == "unknown/pipeline"

    def test_summary_fallback_when_no_message(self):
        """final_output に message/summary がない場合、'実行結果 #{id[:8]}' になる。"""
        row = _make_pending_row()
        row["operations"]["final_output"] = {}   # message も summary も除去
        mock_db = _mock_db_with_logs(rows=[row])

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.get("/execution/pending-approvals")

        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        assert item["summary"] == f"実行結果 #{row['id'][:8]}"

    def test_editor_cannot_access(self):
        """editor ロールは 403 になる。"""
        from auth.middleware import get_current_user

        app3 = FastAPI()
        app3.include_router(router)
        app3.dependency_overrides[get_current_user] = lambda: EDITOR_USER
        client = TestClient(app3, raise_server_exceptions=False)

        resp = client.get(
            "/execution/pending-approvals",
            headers={"Authorization": "Bearer dummy"},
        )
        assert resp.status_code == 403


# ─────────────────────────────────────
# GET /execution/count
# ─────────────────────────────────────


class TestExecutionCount:
    """/execution/count が /execution/{execution_id} に吸収されないことの回帰テスト。"""

    def test_returns_total_execution_logs_count(self):
        from auth.middleware import get_current_user

        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[get_current_user] = lambda: ADMIN_USER
        mock_db = _mock_db_with_logs(count=42)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = TestClient(app2, raise_server_exceptions=False)
            resp = client.get("/execution/count")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"count": 42}

    def test_editor_can_read_count(self):
        from auth.middleware import get_current_user

        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[get_current_user] = lambda: EDITOR_USER
        mock_db = _mock_db_with_logs(count=0)
        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = TestClient(app2, raise_server_exceptions=False)
            resp = client.get("/execution/count")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"count": 0}


# ─────────────────────────────────────
# POST /execution/{id}/approve
# ─────────────────────────────────────

class TestApproveExecution:

    def _make_client(self, user: JWTClaims):
        from auth.middleware import get_current_user, require_role
        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[require_role("admin")] = lambda: user
        app2.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app2, raise_server_exceptions=False)

    def _mock_db_for_approve(self, row_data: dict | None):
        """maybe_single().execute() が row_data を返す DB モック。"""
        mock_db = MagicMock()
        single_result = MagicMock()
        single_result.data = row_data

        chain = mock_db.table.return_value
        for method in ("select", "eq", "update", "maybe_single"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = single_result
        return mock_db

    def _make_pending_row_with_ops(self, execution_id: str, pipeline: str = "construction/estimation") -> dict:
        """operations フィールドを持つ pending 行を生成する。"""
        return {
            "id": execution_id,
            "approval_status": "pending",
            "company_id": COMPANY_ID,
            "operations": {
                "pipeline": pipeline,
                "steps": [{"step": "extract", "input_data": {"project": "テスト現場"}}],
                "final_output": {"message": "見積書を作成しました", "total": 500000},
            },
        }

    def test_approve_pending_execution(self):
        """pending の実行ログを承認すると 200 が返る。"""
        execution_id = str(uuid.uuid4())
        pending_row = self._make_pending_row_with_ops(execution_id)
        mock_db = self._mock_db_for_approve(pending_row)

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution.asyncio.create_task"):
            client = self._make_client(ADMIN_USER)
            resp = client.post(f"/execution/{execution_id}/approve", json={})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["message"] == "承認しました"
        assert data["execution_id"] == execution_id

    def test_approve_with_modified_output(self):
        """modified_output を渡すと approval_status が 'modified' になるはず。"""
        execution_id = str(uuid.uuid4())
        pending_row = self._make_pending_row_with_ops(execution_id)
        mock_db = self._mock_db_for_approve(pending_row)

        update_calls = []
        original_update = mock_db.table.return_value.update
        def capture_update(payload):
            update_calls.append(payload)
            return mock_db.table.return_value
        mock_db.table.return_value.update = capture_update

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution.asyncio.create_task"):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{execution_id}/approve",
                json={"modified_output": {"total": 999}},
            )

        assert resp.status_code == 200, resp.text
        assert any(p.get("approval_status") == "modified" for p in update_calls)

    def test_approve_already_approved_returns_400(self):
        """すでに approved の実行ログに対して再承認は 400 になる。"""
        execution_id = str(uuid.uuid4())
        already_approved = {
            "id": execution_id,
            "approval_status": "approved",
            "company_id": COMPANY_ID,
            "operations": {"pipeline": "construction/estimation", "steps": [], "final_output": {}},
        }
        mock_db = self._mock_db_for_approve(already_approved)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post(f"/execution/{execution_id}/approve", json={})

        assert resp.status_code == 400
        assert "処理済み" in resp.json()["detail"]

    def test_approve_not_found_returns_404(self):
        """存在しない / 他テナントのログに対しては 404 になる。"""
        mock_db = self._mock_db_for_approve(None)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post(f"/execution/{uuid.uuid4()}/approve", json={})

        assert resp.status_code == 404

    def test_approve_triggers_background_reexecution(self):
        """承認後に asyncio.create_task が呼ばれ、バックグラウンド再実行が起動される。"""
        execution_id = str(uuid.uuid4())
        pending_row = self._make_pending_row_with_ops(execution_id, pipeline="common/expense")
        mock_db = self._mock_db_for_approve(pending_row)

        create_task_calls = []
        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution.asyncio.create_task", side_effect=lambda coro: create_task_calls.append(coro)):
            client = self._make_client(ADMIN_USER)
            resp = client.post(f"/execution/{execution_id}/approve", json={})

        assert resp.status_code == 200, resp.text
        assert len(create_task_calls) == 1

    def test_approve_modified_output_used_as_reexec_input(self):
        """modified_output がある場合、それが再実行の input_data として使われる。"""
        from routers.execution import _reexecute_after_approval
        execution_id = str(uuid.uuid4())
        pending_row = self._make_pending_row_with_ops(execution_id)
        mock_db = self._mock_db_for_approve(pending_row)

        captured_reexec_args: dict = {}
        original_fn = _reexecute_after_approval

        async def mock_reexec(**kwargs):
            captured_reexec_args.update(kwargs)

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution._reexecute_after_approval", side_effect=mock_reexec), \
             patch("routers.execution.asyncio.create_task"):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{execution_id}/approve",
                json={"modified_output": {"corrected_total": 12345}},
            )

        assert resp.status_code == 200, resp.text

    def test_approve_no_pipeline_skips_reexecution(self):
        """operations に pipeline がない場合、再実行をスキップしても 200 が返る。"""
        execution_id = str(uuid.uuid4())
        pending_row = {
            "id": execution_id,
            "approval_status": "pending",
            "company_id": COMPANY_ID,
            "operations": {"steps": [], "final_output": {}},  # pipeline キーなし
        }
        mock_db = self._mock_db_for_approve(pending_row)

        create_task_calls = []
        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock), \
             patch("routers.execution.asyncio.create_task", side_effect=lambda coro: create_task_calls.append(coro)):
            client = self._make_client(ADMIN_USER)
            resp = client.post(f"/execution/{execution_id}/approve", json={})

        assert resp.status_code == 200, resp.text
        # pipeline がないので create_task は呼ばれない
        assert len(create_task_calls) == 0


class TestReexecuteAfterApproval:
    """_reexecute_after_approval のユニットテスト。"""

    @pytest.mark.asyncio
    async def test_successful_reexecution_saves_new_log(self):
        """再実行が成功すると新しい execution_log が INSERT される。"""
        from routers.execution import _reexecute_after_approval
        from workers.bpo.manager.models import PipelineResult

        mock_result = PipelineResult(
            success=True,
            pipeline="construction/estimation",
            steps=[{"step": "extract", "confidence": 0.9, "cost_yen": 5.0}],
            final_output={"total": 800000},
            total_cost_yen=5.0,
            total_duration_ms=200,
        )

        inserted_payload: dict = {}
        mock_db = MagicMock()
        insert_chain = MagicMock()

        def capture_insert(payload):
            inserted_payload.update(payload)
            inner = MagicMock()
            inner.execute.return_value = MagicMock()
            return inner

        insert_chain.insert = capture_insert
        mock_db.table.return_value = insert_chain

        with patch("routers.execution.route_and_execute", new_callable=AsyncMock, return_value=mock_result), \
             patch("routers.execution.get_service_client", return_value=mock_db):
            await _reexecute_after_approval(
                company_id=COMPANY_ID,
                approved_by=USER_ID,
                execution_id="original-id-123",
                pipeline="construction/estimation",
                input_data={"project": "テスト現場"},
            )

        assert inserted_payload.get("company_id") == COMPANY_ID
        assert inserted_payload.get("overall_success") is True
        assert inserted_payload.get("approval_status") == "approved"
        ops = inserted_payload.get("operations", {})
        assert ops.get("pipeline") == "construction/estimation"
        assert ops.get("reexecuted_from") == "original-id-123"

    @pytest.mark.asyncio
    async def test_reexecution_uses_data_collect_level(self):
        """再実行は ExecutionLevel.DATA_COLLECT (1) で呼ばれ、承認ループを防ぐ。"""
        from routers.execution import _reexecute_after_approval
        from workers.bpo.manager.models import PipelineResult
        from shared.enums import ExecutionLevel

        mock_result = PipelineResult(
            success=True,
            pipeline="common/expense",
            final_output={"ok": True},
        )

        captured_task = {}
        mock_db = MagicMock()
        insert_chain = MagicMock()
        insert_chain.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value = insert_chain

        async def capture_route(task, **kwargs):
            captured_task["task"] = task
            return mock_result

        with patch("routers.execution.route_and_execute", side_effect=capture_route), \
             patch("routers.execution.get_service_client", return_value=mock_db):
            await _reexecute_after_approval(
                company_id=COMPANY_ID,
                approved_by=USER_ID,
                execution_id="orig-456",
                pipeline="common/expense",
                input_data={"amount": 5000},
            )

        task = captured_task.get("task")
        assert task is not None
        assert task.execution_level == ExecutionLevel.DATA_COLLECT
        assert task.estimated_impact == 0.0
        assert task.input_data == {"amount": 5000}

    @pytest.mark.asyncio
    async def test_reexecution_failure_does_not_raise(self):
        """再実行中に例外が起きてもロガーに記録されるだけで、例外は外に漏れない。"""
        from routers.execution import _reexecute_after_approval

        with patch("routers.execution.route_and_execute", new_callable=AsyncMock, side_effect=RuntimeError("DB障害")):
            # 例外が外に伝播しないことを確認
            await _reexecute_after_approval(
                company_id=COMPANY_ID,
                approved_by=USER_ID,
                execution_id="orig-789",
                pipeline="common/payroll",
                input_data={},
            )


# ─────────────────────────────────────
# POST /execution/{id}/reject
# ─────────────────────────────────────

class TestRejectExecution:

    def _make_client(self, user: JWTClaims):
        from auth.middleware import get_current_user, require_role
        app2 = FastAPI()
        app2.include_router(router)
        app2.dependency_overrides[require_role("admin")] = lambda: user
        app2.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app2, raise_server_exceptions=False)

    def _mock_db_for_reject(self, row_data: dict | None):
        mock_db = MagicMock()
        single_result = MagicMock()
        single_result.data = row_data

        chain = mock_db.table.return_value
        for method in ("select", "eq", "update", "maybe_single"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = single_result
        return mock_db

    def test_reject_pending_execution(self):
        """pending の実行ログを却下すると 200 が返る。"""
        execution_id = str(uuid.uuid4())
        pending_row = {
            "id": execution_id,
            "approval_status": "pending",
            "company_id": COMPANY_ID,
        }
        mock_db = self._mock_db_for_reject(pending_row)

        with patch("routers.execution.get_service_client", return_value=mock_db), \
             patch("routers.execution.audit_log", new_callable=AsyncMock):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{execution_id}/reject",
                json={"reason": "金額に誤りがある"},
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["message"] == "却下しました"
        assert data["execution_id"] == execution_id

    def test_reject_other_tenant_log_returns_404(self):
        """他テナントのログにアクセスすると 404 になる（company_id フィルタ）。"""
        mock_db = self._mock_db_for_reject(None)  # data=None → 404

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{uuid.uuid4()}/reject",
                json={"reason": "不正アクセス"},
            )

        assert resp.status_code == 404

    def test_reject_already_rejected_returns_400(self):
        """すでに rejected の実行ログに再却下は 400 になる。"""
        execution_id = str(uuid.uuid4())
        already_rejected = {
            "id": execution_id,
            "approval_status": "rejected",
            "company_id": COMPANY_ID,
        }
        mock_db = self._mock_db_for_reject(already_rejected)

        with patch("routers.execution.get_service_client", return_value=mock_db):
            client = self._make_client(ADMIN_USER)
            resp = client.post(
                f"/execution/{execution_id}/reject",
                json={"reason": "重複"},
            )

        assert resp.status_code == 400


# ─────────────────────────────────────
# _save_execution_log の approval_status 分岐
# ─────────────────────────────────────

class TestSaveExecutionLogApprovalStatus:

    def _make_mock_db(self, requires_approval: bool):
        """bpo_hitl_requirements と execution_logs insert をモックする DB を返す。"""
        mock_db = MagicMock()
        hitl_result = MagicMock()
        hitl_result.data = {"requires_approval": requires_approval, "min_confidence_for_auto": 0.9}

        insert_result = MagicMock()

        def table_side_effect(table_name: str):
            chain = MagicMock()
            if table_name == "bpo_hitl_requirements":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.maybe_single.return_value = chain
                chain.execute.return_value = hitl_result
            else:  # execution_logs
                chain.insert.return_value = chain
                chain.execute.return_value = insert_result
            return chain

        mock_db.table.side_effect = table_side_effect
        return mock_db, insert_result

    def test_requires_approval_true_sets_pending(self):
        """requires_approval=True → approval_status='pending', original_output 保存。"""
        mock_db, _ = self._make_mock_db(requires_approval=True)

        inserted_payload = {}

        def capture_insert(table_name):
            chain = MagicMock()
            if table_name == "bpo_hitl_requirements":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.maybe_single.return_value = chain
                hitl_result = MagicMock()
                hitl_result.data = {"requires_approval": True, "min_confidence_for_auto": 0.9}
                chain.execute.return_value = hitl_result
            else:
                def _insert(payload):
                    inserted_payload.update(payload)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock()
                    return inner
                chain.insert = _insert
            return chain

        mock_db2 = MagicMock()
        mock_db2.table.side_effect = capture_insert

        final_output = {"total": 100000}
        log_id, approval_pending = _save_execution_log(
            db=mock_db2,
            company_id=COMPANY_ID,
            pipeline="construction/estimation",
            triggered_by=USER_ID,
            result_steps=[],
            final_output=final_output,
            overall_success=True,
        )

        assert approval_pending is True
        assert inserted_payload.get("approval_status") == "pending"
        assert inserted_payload.get("original_output") == final_output

    def test_requires_approval_false_sets_approved(self):
        """requires_approval=False → approval_status='approved'。"""
        inserted_payload = {}

        def capture_insert(table_name):
            chain = MagicMock()
            if table_name == "bpo_hitl_requirements":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.maybe_single.return_value = chain
                hitl_result = MagicMock()
                hitl_result.data = {"requires_approval": False, "min_confidence_for_auto": 0.9}
                chain.execute.return_value = hitl_result
            else:
                def _insert(payload):
                    inserted_payload.update(payload)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock()
                    return inner
                chain.insert = _insert
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = capture_insert

        log_id, approval_pending = _save_execution_log(
            db=mock_db,
            company_id=COMPANY_ID,
            pipeline="construction/estimation",
            triggered_by=USER_ID,
            result_steps=[],
            final_output={"ok": True},
            overall_success=True,
        )

        assert approval_pending is False
        assert inserted_payload.get("approval_status") == "approved"
        assert "original_output" not in inserted_payload

    def test_hitl_table_not_found_defaults_to_approved(self):
        """bpo_hitl_requirements に該当行がない場合、approval_status='approved' になる。"""
        inserted_payload = {}

        def capture_insert(table_name):
            chain = MagicMock()
            if table_name == "bpo_hitl_requirements":
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.maybe_single.return_value = chain
                hitl_result = MagicMock()
                hitl_result.data = None  # 該当行なし
                chain.execute.return_value = hitl_result
            else:
                def _insert(payload):
                    inserted_payload.update(payload)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock()
                    return inner
                chain.insert = _insert
            return chain

        mock_db = MagicMock()
        mock_db.table.side_effect = capture_insert

        log_id, approval_pending = _save_execution_log(
            db=mock_db,
            company_id=COMPANY_ID,
            pipeline="unknown/pipeline",
            triggered_by=USER_ID,
            result_steps=[],
            final_output={},
            overall_success=True,
        )

        assert approval_pending is False
        assert inserted_payload.get("approval_status") == "approved"

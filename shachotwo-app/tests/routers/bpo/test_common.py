"""共通バックオフィスBPOルーター tests/routers/bpo/test_common.py"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt import JWTClaims
from routers.bpo.common import router
from workers.bpo.manager.models import PipelineResult


# ─────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

MOCK_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)


def _make_client(user: JWTClaims = MOCK_USER) -> TestClient:
    from auth.middleware import get_current_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _make_pipeline_result(pipeline: str, success: bool = True, output: dict | None = None) -> PipelineResult:
    return PipelineResult(
        success=success,
        pipeline=pipeline,
        steps=[],
        final_output=output or {},
    )


def _mock_db(rows: list[dict] | None = None) -> MagicMock:
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.data = rows or []
    chain = mock_db.table.return_value
    for method in ("select", "eq", "order", "range"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    return mock_db


# ─────────────────────────────────────
# POST /attendance/process
# ─────────────────────────────────────

class TestProcessAttendance:

    def test_success_returns_200(self):
        """正常系: 勤怠処理が成功し 200 を返す"""
        mock_result = _make_pipeline_result(
            "common/attendance",
            output={"processed_count": 20, "anomalies": []},
        )

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            client = _make_client()
            resp = client.post("/attendance/process", json={
                "target_month": "202603",
                "department": "営業部",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert "processed_count" in data["output"]

    def test_route_and_execute_called_with_attendance_pipeline(self):
        """common/attendance パイプラインが route_and_execute に渡される"""
        mock_result = _make_pipeline_result("common/attendance")

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_route:
            client = _make_client()
            client.post("/attendance/process", json={"target_month": "202603"})

        mock_route.assert_awaited_once()
        task = mock_route.call_args.args[0]
        assert task.pipeline == "common/attendance"
        assert task.company_id == COMPANY_ID

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す"""
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/attendance/process", json={"target_month": "202603"})
        assert resp.status_code == 401


# ─────────────────────────────────────
# POST /contracts/analyze
# ─────────────────────────────────────

class TestAnalyzeContract:

    def test_success_returns_200(self):
        """正常系: 契約書分析が成功し 200 を返す"""
        mock_result = _make_pipeline_result(
            "common/contract",
            output={
                "risk_level": "medium",
                "risk_alerts": ["自動更新条項あり", "解約通知期間3ヶ月"],
            },
        )

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            client = _make_client()
            resp = client.post("/contracts/analyze", json={
                "contract_text": "本契約は甲乙間で締結する業務委託契約書である。",
                "contract_type": "業務委託",
                "counterparty": "株式会社テスト",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["output"]["risk_level"] == "medium"

    def test_route_and_execute_called_with_contract_pipeline(self):
        """common/contract パイプラインが route_and_execute に渡される"""
        mock_result = _make_pipeline_result("common/contract")

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_route:
            client = _make_client()
            client.post("/contracts/analyze", json={
                "contract_text": "テスト契約書",
            })

        mock_route.assert_awaited_once()
        task = mock_route.call_args.args[0]
        assert task.pipeline == "common/contract"
        assert task.input_data["contract_text"] == "テスト契約書"

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す"""
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/contracts/analyze", json={"contract_text": "テスト"})
        assert resp.status_code == 401


# ─────────────────────────────────────
# GET /contracts
# ─────────────────────────────────────

class TestListContracts:

    def test_returns_empty_list(self):
        """契約書0件の場合も正常に空リストを返す"""
        mock_db_instance = _mock_db(rows=[])

        with patch("routers.bpo.common.get_service_client", return_value=mock_db_instance):
            client = _make_client()
            resp = client.get("/contracts")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_filters_contract_pipeline_only(self):
        """common/contract 以外のパイプラインは除外される"""
        rows = [
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "common/expense",
                    "input_data": {},
                    "final_output": {},
                },
                "overall_success": True,
                "created_at": "2026-03-01T00:00:00",
            },
        ]
        mock_db_instance = _mock_db(rows=rows)

        with patch("routers.bpo.common.get_service_client", return_value=mock_db_instance):
            client = _make_client()
            resp = client.get("/contracts")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_contract_with_risk_alerts(self):
        """common/contract パイプラインの結果がリスクアラート付きで返る"""
        contract_id = str(uuid.uuid4())
        rows = [
            {
                "id": contract_id,
                "operations": {
                    "pipeline": "common/contract",
                    "input_data": {
                        "counterparty": "ABC商事",
                        "contract_type": "売買契約",
                    },
                    "final_output": {
                        "risk_level": "high",
                        "risk_alerts": ["違約金条項あり"],
                    },
                },
                "overall_success": True,
                "created_at": "2026-03-15T10:00:00",
            },
        ]
        mock_db_instance = _mock_db(rows=rows)

        with patch("routers.bpo.common.get_service_client", return_value=mock_db_instance):
            client = _make_client()
            resp = client.get("/contracts")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == contract_id
        assert data[0]["risk_level"] == "high"
        assert data[0]["counterparty"] == "ABC商事"

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す"""
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/contracts")
        assert resp.status_code == 401


# ─────────────────────────────────────
# POST /expense/process
# ─────────────────────────────────────

class TestProcessExpense:

    def test_success_returns_200(self):
        """正常系: 経費処理が成功し 200 を返す"""
        mock_result = _make_pipeline_result(
            "common/expense",
            output={"expense_id": str(uuid.uuid4()), "status": "approved"},
        )

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            client = _make_client()
            resp = client.post("/expense/process", json={
                "amount": 5500.0,
                "category": "交通費",
                "description": "客先訪問（新宿→渋谷）",
                "receipt_date": "2026-03-15",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["output"]["status"] == "approved"

    def test_route_and_execute_called_with_expense_pipeline(self):
        """common/expense パイプラインが route_and_execute に渡される"""
        mock_result = _make_pipeline_result("common/expense")

        with patch(
            "routers.bpo.common.route_and_execute",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_route:
            client = _make_client()
            client.post("/expense/process", json={
                "amount": 3000.0,
                "category": "接待費",
                "description": "顧客懇親会",
            })

        mock_route.assert_awaited_once()
        task = mock_route.call_args.args[0]
        assert task.pipeline == "common/expense"
        assert task.input_data["amount"] == 3000.0
        assert task.input_data["category"] == "接待費"

    def test_no_auth_returns_401(self):
        """認証なしは 401 を返す"""
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/expense/process", json={
            "amount": 1000.0,
            "category": "消耗品費",
            "description": "コピー用紙",
        })
        assert resp.status_code == 401

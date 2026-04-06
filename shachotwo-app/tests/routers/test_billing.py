"""Tests for routers/billing.py — ARPU scale / usage metrics."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.billing import router
from auth.jwt import JWTClaims
from auth.middleware import get_current_user

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPANY_ID = str(uuid4())
USER_ID = str(uuid4())

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


def _make_client(user: JWTClaims = ADMIN_USER) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _mock_db(data: list | None = None) -> MagicMock:
    """Supabase チェーンモック（select/eq/order/limit/insert/execute）。"""
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "order", "limit", "insert"):
        getattr(chain, m).return_value = chain
    result = MagicMock()
    result.data = data or []
    chain.execute.return_value = result
    db.table.return_value = chain
    return db


# ---------------------------------------------------------------------------
# GET /billing/usage
# ---------------------------------------------------------------------------

class TestGetUsageSummary:
    def test_returns_summary_with_all_metric_types(self):
        rows = [
            {"metric_type": "pipeline_run", "quantity": 10},
            {"metric_type": "qa_query", "quantity": 50},
        ]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["company_id"] == COMPANY_ID
        # 4種類のmetric_typeが全て含まれること
        metric_types = {m["metric_type"] for m in body["metrics"]}
        assert metric_types == {"pipeline_run", "connector_sync", "qa_query", "seat"}

    def test_overage_calculated_correctly(self):
        # pipeline_run を基本枠(300)を超えて400件
        rows = [{"metric_type": "pipeline_run", "quantity": 400}]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage")
        body = resp.json()
        pipeline = next(m for m in body["metrics"] if m["metric_type"] == "pipeline_run")
        assert pipeline["total_quantity"] == 400
        assert pipeline["free_quota"] == 300
        assert pipeline["billable_quantity"] == 100       # 400 - 300
        assert pipeline["subtotal_yen"] == 100 * 500      # 50,000円
        assert body["overage_yen"] == 100 * 500

    def test_within_free_quota_no_overage(self):
        # pipeline_run が基本枠内(100件)
        rows = [{"metric_type": "pipeline_run", "quantity": 100}]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage")
        body = resp.json()
        pipeline = next(m for m in body["metrics"] if m["metric_type"] == "pipeline_run")
        assert pipeline["billable_quantity"] == 0
        assert pipeline["subtotal_yen"] == 0
        assert body["overage_yen"] == 0

    def test_period_month_query_param(self):
        mock_db = _mock_db([])
        with patch("routers.billing.get_service_client", return_value=mock_db) as mock_get:
            resp = _make_client().get("/billing/usage?period_month=2025-12")
        assert resp.status_code == 200
        # period_month が指定値で返ること
        assert resp.json()["period_month"] == "2025-12"

    def test_db_error_returns_500(self):
        db = MagicMock()
        db.table.side_effect = Exception("DB接続失敗")
        with patch("routers.billing.get_service_client", return_value=db):
            resp = _make_client().get("/billing/usage")
        assert resp.status_code == 500

    def test_base_plan_yen_is_always_zero(self):
        """base_plan_yen は billing router では 0 固定（別テーブル管理）。"""
        mock_db = _mock_db([])
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage")
        assert resp.json()["base_plan_yen"] == 0


# ---------------------------------------------------------------------------
# GET /billing/usage/history
# ---------------------------------------------------------------------------

class TestGetUsageHistory:
    def test_returns_monthly_breakdown(self):
        rows = [
            {"metric_type": "pipeline_run", "quantity": 10, "period_month": "2026-03"},
            {"metric_type": "qa_query", "quantity": 20, "period_month": "2026-03"},
            {"metric_type": "pipeline_run", "quantity": 5, "period_month": "2026-02"},
        ]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["company_id"] == COMPANY_ID
        months = {m["period_month"]: m for m in body["months"]}
        assert "2026-03" in months
        assert "2026-02" in months
        assert months["2026-03"]["breakdown"]["pipeline_run"] == 10
        assert months["2026-03"]["breakdown"]["qa_query"] == 20

    def test_months_param_limits_results(self):
        # 10ヶ月分のデータを返すモックでmonths=2を指定 → 2件のみ返ること
        rows = [
            {"metric_type": "pipeline_run", "quantity": 1, "period_month": f"2026-{i:02d}"}
            for i in range(1, 11)
        ]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage/history?months=2")
        body = resp.json()
        assert len(body["months"]) <= 2

    def test_empty_history(self):
        mock_db = _mock_db([])
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage/history")
        assert resp.status_code == 200
        assert resp.json()["months"] == []

    def test_overage_calculation_in_history(self):
        # qa_query 600件(基本枠500超の100件分課金)
        rows = [{"metric_type": "qa_query", "quantity": 600, "period_month": "2026-03"}]
        mock_db = _mock_db(rows)
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().get("/billing/usage/history")
        months = {m["period_month"]: m for m in resp.json()["months"]}
        assert months["2026-03"]["total_yen"] == 100 * 100  # 100件 × 100円


# ---------------------------------------------------------------------------
# POST /billing/usage/track
# ---------------------------------------------------------------------------

class TestTrackUsage:
    def test_track_pipeline_run_success(self):
        inserted = {
            "id": str(uuid4()),
            "company_id": COMPANY_ID,
            "metric_type": "pipeline_run",
            "quantity": 1,
            "period_month": "2026-04",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_db = _mock_db([inserted])
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().post("/billing/usage/track", json={
                "metric_type": "pipeline_run",
                "quantity": 1,
                "pipeline_name": "construction/estimation",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric_type"] == "pipeline_run"
        assert body["company_id"] == COMPANY_ID

    def test_track_with_period_month_override(self):
        inserted = {
            "id": str(uuid4()),
            "company_id": COMPANY_ID,
            "metric_type": "qa_query",
            "quantity": 3,
            "period_month": "2025-12",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_db = _mock_db([inserted])
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client().post("/billing/usage/track", json={
                "metric_type": "qa_query",
                "quantity": 3,
                "period_month": "2025-12",
            })
        assert resp.status_code == 200
        assert resp.json()["period_month"] == "2025-12"

    def test_invalid_metric_type_returns_422(self):
        resp = _make_client().post("/billing/usage/track", json={
            "metric_type": "invalid_type",
            "quantity": 1,
        })
        assert resp.status_code == 422

    def test_quantity_zero_returns_422(self):
        resp = _make_client().post("/billing/usage/track", json={
            "metric_type": "pipeline_run",
            "quantity": 0,
        })
        assert resp.status_code == 422

    def test_all_valid_metric_types_accepted(self):
        valid_types = ["pipeline_run", "connector_sync", "qa_query", "seat"]
        for mt in valid_types:
            inserted = {
                "id": str(uuid4()),
                "company_id": COMPANY_ID,
                "metric_type": mt,
                "quantity": 1,
                "period_month": "2026-04",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            mock_db = _mock_db([inserted])
            with patch("routers.billing.get_service_client", return_value=mock_db):
                resp = _make_client().post("/billing/usage/track", json={
                    "metric_type": mt,
                    "quantity": 1,
                })
            assert resp.status_code == 200, f"metric_type={mt} should be accepted"

    def test_editor_role_can_track(self):
        inserted = {
            "id": str(uuid4()),
            "company_id": COMPANY_ID,
            "metric_type": "pipeline_run",
            "quantity": 1,
            "period_month": "2026-04",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_db = _mock_db([inserted])
        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(EDITOR_USER).post("/billing/usage/track", json={
                "metric_type": "pipeline_run",
                "quantity": 1,
            })
        assert resp.status_code == 200

    def test_db_error_returns_500(self):
        db = MagicMock()
        db.table.side_effect = Exception("DB書き込み失敗")
        with patch("routers.billing.get_service_client", return_value=db):
            resp = _make_client().post("/billing/usage/track", json={
                "metric_type": "pipeline_run",
                "quantity": 1,
            })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 手動請求（口座振替・請求書払い）
# ---------------------------------------------------------------------------

def _mock_db_manual(
    insert_data: list | None = None,
    fetch_data: list | None = None,
    update_data: list | None = None,
) -> MagicMock:
    """手動請求テスト用 Supabase チェーンモック。
    insert/fetch(select)/update で異なる data を返せる。
    """
    db = MagicMock()

    def _make_chain(data: list | None):
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit", "insert", "update", "not_"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = data or []
        chain.execute.return_value = result
        return chain

    # table() が呼ばれるたびに同じテーブルチェーンを返す
    # insert → insert_data, select(fetch) → fetch_data, update → update_data
    # 呼び出し順に返すため side_effect を使う
    chains = []
    for d in [insert_data, fetch_data, update_data]:
        if d is not None:
            chains.append(_make_chain(d))

    if not chains:
        chains = [_make_chain([])]

    db.table.side_effect = [c for c in chains] + [_make_chain([])] * 10
    return db


INVOICE_ID = str(uuid4())

_SAMPLE_INVOICE_ROW = {
    "id": INVOICE_ID,
    "company_id": COMPANY_ID,
    "amount_yen": 300000,
    "description": "2026年4月分 業種特化BPO利用料",
    "due_date": "2026-04-30",
    "payment_method": "bank_transfer",
    "bank_info": {"bank_name": "三菱UFJ銀行", "account_number": "1234567"},
    "status": "pending",
    "paid_at": None,
    "created_by": USER_ID,
    "created_at": "2026-04-01T00:00:00+00:00",
    "updated_at": "2026-04-01T00:00:00+00:00",
}


class TestManualInvoiceCreate:
    """POST /billing/invoices/manual"""

    def test_manual_invoice_created(self):
        """正常系: 手動請求書が作成され status=pending で返る。"""
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit", "insert", "update"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = [_SAMPLE_INVOICE_ROW]
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).post("/billing/invoices/manual", json={
                "amount_yen": 300000,
                "description": "2026年4月分 業種特化BPO利用料",
                "due_date": "2026-04-30",
                "payment_method": "bank_transfer",
                "bank_info": {"bank_name": "三菱UFJ銀行", "account_number": "1234567"},
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["amount_yen"] == 300000
        assert body["payment_method"] == "bank_transfer"
        assert body["paid_at"] is None

    def test_invoice_payment_method(self):
        """payment_method=invoice でも作成できる。"""
        row = {**_SAMPLE_INVOICE_ROW, "payment_method": "invoice", "bank_info": None}
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit", "insert", "update"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = [row]
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).post("/billing/invoices/manual", json={
                "amount_yen": 150000,
                "description": "共通BPO利用料",
                "due_date": "2026-04-30",
                "payment_method": "invoice",
            })

        assert resp.status_code == 200
        assert resp.json()["payment_method"] == "invoice"

    def test_invalid_payment_method_returns_422(self):
        """不正な payment_method は 422。"""
        resp = _make_client(ADMIN_USER).post("/billing/invoices/manual", json={
            "amount_yen": 300000,
            "description": "test",
            "due_date": "2026-04-30",
            "payment_method": "credit_card",  # 不正値
        })
        assert resp.status_code == 422

    def test_amount_zero_returns_422(self):
        """amount_yen=0 は 422。"""
        resp = _make_client(ADMIN_USER).post("/billing/invoices/manual", json={
            "amount_yen": 0,
            "description": "test",
            "due_date": "2026-04-30",
            "payment_method": "bank_transfer",
        })
        assert resp.status_code == 422

    def test_editor_cannot_create_invoice(self):
        """editor ロールは請求書を発行できない（403）。"""
        resp = _make_client(EDITOR_USER).post("/billing/invoices/manual", json={
            "amount_yen": 300000,
            "description": "test",
            "due_date": "2026-04-30",
            "payment_method": "bank_transfer",
        })
        assert resp.status_code == 403


class TestManualInvoiceMarkPaid:
    """PATCH /billing/invoices/{invoice_id}/paid"""

    def test_manual_invoice_marked_as_paid(self):
        """正常系: 請求書を支払済みにすると status=paid・paid_at が設定される。"""
        paid_row = {**_SAMPLE_INVOICE_ROW, "status": "paid", "paid_at": "2026-04-10T09:00:00+00:00"}

        mock_db = MagicMock()
        # 1回目の table() → fetch（存在確認）、2回目 → update
        fetch_chain = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(fetch_chain, m).return_value = fetch_chain
        fetch_result = MagicMock()
        fetch_result.data = [_SAMPLE_INVOICE_ROW]
        fetch_chain.execute.return_value = fetch_result

        update_chain = MagicMock()
        for m in ("update", "eq", "order", "limit"):
            getattr(update_chain, m).return_value = update_chain
        update_result = MagicMock()
        update_result.data = [paid_row]
        update_chain.execute.return_value = update_result

        mock_db.table.side_effect = [fetch_chain, update_chain]

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).patch(f"/billing/invoices/{INVOICE_ID}/paid")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "paid"
        assert body["paid_at"] is not None

    def test_mark_paid_not_found_returns_404(self):
        """存在しない invoice_id は 404。"""
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = []   # 空 → 404
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).patch(f"/billing/invoices/{uuid4()}/paid")

        assert resp.status_code == 404

    def test_editor_cannot_mark_paid(self):
        """editor は支払済み操作不可（403）。"""
        resp = _make_client(EDITOR_USER).patch(f"/billing/invoices/{INVOICE_ID}/paid")
        assert resp.status_code == 403


class TestManualInvoiceList:
    """GET /billing/invoices/manual"""

    def test_manual_invoice_list(self):
        """正常系: company_id でフィルタされた請求書一覧が返る。"""
        rows = [
            _SAMPLE_INVOICE_ROW,
            {**_SAMPLE_INVOICE_ROW, "id": str(uuid4()), "status": "paid",
             "paid_at": "2026-03-31T10:00:00+00:00"},
        ]
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = rows
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).get("/billing/invoices/manual")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["company_id"] == COMPANY_ID
        statuses = {inv["status"] for inv in body}
        assert statuses == {"pending", "paid"}

    def test_empty_list(self):
        """請求書がない場合は空リストを返す。"""
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = []
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(ADMIN_USER).get("/billing/invoices/manual")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_editor_can_view_invoices(self):
        """editor ロールも一覧参照は可能。"""
        mock_db = MagicMock()
        chain = MagicMock()
        for m in ("select", "eq", "order", "limit"):
            getattr(chain, m).return_value = chain
        result = MagicMock()
        result.data = [_SAMPLE_INVOICE_ROW]
        chain.execute.return_value = result
        mock_db.table.return_value = chain

        with patch("routers.billing.get_service_client", return_value=mock_db):
            resp = _make_client(EDITOR_USER).get("/billing/invoices/manual")

        assert resp.status_code == 200

    def test_db_error_returns_500(self):
        """DB エラー時は 500。"""
        db = MagicMock()
        db.table.side_effect = Exception("DB接続失敗")
        with patch("routers.billing.get_service_client", return_value=db):
            resp = _make_client(ADMIN_USER).get("/billing/invoices/manual")
        assert resp.status_code == 500

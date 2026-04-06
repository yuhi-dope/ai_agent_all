"""Tests for routers/partner.py — Partner Marketplace。
全テスト: Supabase モック使用、DB接続なし。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.partner import router
from auth.jwt import JWTClaims
from auth.middleware import get_current_user

# ---------------------------------------------------------------------------
# 固定ID（テスト全体で共通）
# ---------------------------------------------------------------------------
COMPANY_ID = str(uuid4())
PARTNER_COMPANY_ID = str(uuid4())   # パートナー自身の会社
USER_ID = str(uuid4())
PARTNER_ID = str(uuid4())
APP_ID = str(uuid4())
INSTALL_ID = str(uuid4())
REVIEW_ID = str(uuid4())
REVENUE_ID = str(uuid4())

# ---------------------------------------------------------------------------
# JWTClaims フィクスチャ
# ---------------------------------------------------------------------------
ADMIN_USER = JWTClaims(
    sub=USER_ID,
    company_id=COMPANY_ID,
    role="admin",
    email="admin@example.com",
)

PARTNER_USER = JWTClaims(
    sub=USER_ID,
    company_id=PARTNER_COMPANY_ID,
    role="admin",
    email="partner@example.com",
)

# ---------------------------------------------------------------------------
# テストクライアント生成ヘルパー
# ---------------------------------------------------------------------------

def _make_client(user: JWTClaims = ADMIN_USER) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Supabase チェーンモック生成ヘルパー
# ---------------------------------------------------------------------------

def _mock_db(data: list | None = None) -> MagicMock:
    """Supabase チェーンモック（select/eq/order/limit/lte/insert/update/execute）。"""
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "order", "limit", "lte", "insert", "update"):
        getattr(chain, m).return_value = chain
    result = MagicMock()
    result.data = data if data is not None else []
    chain.execute.return_value = result
    db.table.return_value = chain
    return db


def _mock_db_multi(*datasets: list | None) -> MagicMock:
    """複数の execute 呼び出しに対して順番にデータを返す多段モック。"""
    db = MagicMock()
    chain = MagicMock()
    for m in ("select", "eq", "order", "limit", "lte", "insert", "update"):
        getattr(chain, m).return_value = chain

    results = []
    for data in datasets:
        r = MagicMock()
        r.data = data if data is not None else []
        results.append(r)

    chain.execute.side_effect = results
    db.table.return_value = chain
    return db


# ---------------------------------------------------------------------------
# サンプルデータ
# ---------------------------------------------------------------------------

PARTNER_ROW = {
    "id": PARTNER_ID,
    "company_id": PARTNER_COMPANY_ID,
    "display_name": "テスト社労士事務所",
    "partner_type": "sharoushi",
    "contact_email": "test@sharoushi.example.com",
    "revenue_share_rate": 0.700,
    "is_approved": True,
    "approved_at": "2026-01-01T00:00:00+00:00",
    "created_at": "2026-01-01T00:00:00+00:00",
}

UNAPPROVED_PARTNER_ROW = {**PARTNER_ROW, "is_approved": False, "approved_at": None}

APP_ROW = {
    "id": APP_ID,
    "partner_id": PARTNER_ID,
    "name": "労務管理BPOパック",
    "description": "社労士が提供する労務管理自動化",
    "category": "bpo",
    "price_yen": 50000,
    "pricing_model": "monthly",
    "genome_config": None,
    "pipeline_config": None,
    "icon_url": None,
    "status": "published",
    "install_count": 10,
    "rating_avg": 4.5,
    "created_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}

DRAFT_APP_ROW = {**APP_ROW, "status": "draft", "install_count": 0, "rating_avg": None}

INSTALL_ROW = {
    "id": INSTALL_ID,
    "app_id": APP_ID,
    "company_id": COMPANY_ID,
    "installed_at": "2026-02-01T00:00:00+00:00",
    "config": None,
    "is_active": True,
}

REVIEW_ROW = {
    "id": REVIEW_ID,
    "app_id": APP_ID,
    "company_id": COMPANY_ID,
    "reviewer_user_id": USER_ID,
    "rating": 5,
    "comment": "とても使いやすいです",
    "created_at": "2026-02-01T00:00:00+00:00",
}

REVENUE_ROW = {
    "id": REVENUE_ID,
    "partner_id": PARTNER_ID,
    "app_id": APP_ID,
    "company_id": COMPANY_ID,
    "period_month": "2026-03",
    "gross_amount_yen": 50000,
    "partner_amount_yen": 35000,
    "platform_amount_yen": 15000,
    "revenue_share_rate": 0.700,
    "stripe_payout_id": None,
    "status": "paid",
    "paid_at": "2026-04-01T00:00:00+00:00",
    "created_at": "2026-04-01T00:00:00+00:00",
}


# ===========================================================================
# test_partner_register
# ===========================================================================

class TestPartnerRegister:
    def test_register_success(self):
        """新規パートナー登録が 201 で返る。"""
        # 1回目: 既存チェック（なし） / 2回目: insert
        mock_db = _mock_db_multi([], [PARTNER_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/register",
                json={
                    "display_name": "テスト社労士事務所",
                    "partner_type": "sharoushi",
                    "contact_email": "test@sharoushi.example.com",
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["display_name"] == "テスト社労士事務所"
        assert body["partner_type"] == "sharoushi"

    def test_register_duplicate_returns_409(self):
        """すでに登録済みの場合 409 を返す。"""
        mock_db = _mock_db([PARTNER_ROW])  # 既存あり
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/register",
                json={"display_name": "テスト社労士事務所", "partner_type": "sharoushi"},
            )
        assert resp.status_code == 409

    def test_register_invalid_partner_type_returns_422(self):
        """無効な partner_type は 422 を返す。"""
        mock_db = _mock_db([])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/register",
                json={"display_name": "テスト", "partner_type": "invalid_type"},
            )
        assert resp.status_code == 422


# ===========================================================================
# test_create_app_draft
# ===========================================================================

class TestCreateAppDraft:
    def test_create_draft_success(self):
        """承認済みパートナーがアプリを draft 状態で作成できる。"""
        # 1回目: partner取得（承認済み） / 2回目: insert
        mock_db = _mock_db_multi([PARTNER_ROW], [DRAFT_APP_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/apps",
                json={
                    "name": "労務管理BPOパック",
                    "description": "社労士が提供する労務管理自動化",
                    "category": "bpo",
                    "price_yen": 50000,
                    "pricing_model": "monthly",
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["name"] == "労務管理BPOパック"

    def test_create_app_unapproved_partner_returns_403(self):
        """未承認パートナーはアプリ作成できない（403）。"""
        mock_db = _mock_db([UNAPPROVED_PARTNER_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/apps",
                json={"name": "test", "category": "bpo", "price_yen": 0},
            )
        assert resp.status_code == 403

    def test_create_app_not_registered_returns_403(self):
        """パートナー未登録の場合 403 を返す。"""
        mock_db = _mock_db([])  # パートナーなし
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/apps",
                json={"name": "test", "category": "bpo", "price_yen": 0},
            )
        assert resp.status_code == 403

    def test_create_app_invalid_category_returns_422(self):
        """無効な category は 422 を返す。"""
        mock_db = _mock_db_multi([PARTNER_ROW], [])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).post(
                "/partner/apps",
                json={"name": "test", "category": "invalid_category", "price_yen": 0},
            )
        assert resp.status_code == 422


# ===========================================================================
# test_marketplace_list_only_published
# ===========================================================================

class TestMarketplaceListOnlyPublished:
    def test_list_returns_only_published(self):
        """Marketplace 一覧は status=published のアプリのみ返す。"""
        # DB側RLSで published のみ返す想定だが、ルーター側でも eq("status","published") を確認
        mock_db = _mock_db([APP_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().get("/marketplace/apps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "published"

    def test_list_empty_when_no_published(self):
        """公開済みアプリが0件の場合は空リストを返す。"""
        mock_db = _mock_db([])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().get("/marketplace/apps")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_list_filter_by_category(self):
        """category フィルタが動作する。"""
        mock_db = _mock_db([APP_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().get("/marketplace/apps?category=bpo")
        assert resp.status_code == 200

    def test_list_invalid_category_returns_422(self):
        """無効な category フィルタは 422 を返す。"""
        mock_db = _mock_db([])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().get("/marketplace/apps?category=invalid")
        assert resp.status_code == 422

    def test_list_filter_by_max_price(self):
        """max_price_yen フィルタが動作する。"""
        mock_db = _mock_db([APP_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().get("/marketplace/apps?max_price_yen=100000")
        assert resp.status_code == 200


# ===========================================================================
# test_install_app
# ===========================================================================

class TestInstallApp:
    def test_install_success(self):
        """公開済みアプリを新規インストールできる（201）。"""
        # 1回目: app取得（published） / 2回目: 既存インストールチェック（なし） / 3回目: insert / 4回目: install_count更新
        mock_db = _mock_db_multi(
            [APP_ROW],          # app取得
            [],                 # 既存インストールなし
            [INSTALL_ROW],      # insert
            [APP_ROW],          # install_count更新
        )
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(f"/marketplace/apps/{APP_ID}/install", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert body["app_id"] == APP_ID
        assert body["company_id"] == COMPANY_ID
        assert body["is_active"] is True

    def test_install_not_published_returns_403(self):
        """published でないアプリはインストール不可（403）。"""
        mock_db = _mock_db([DRAFT_APP_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(f"/marketplace/apps/{APP_ID}/install", json={})
        assert resp.status_code == 403

    def test_install_app_not_found_returns_404(self):
        """存在しないアプリは 404 を返す。"""
        mock_db = _mock_db([])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(f"/marketplace/apps/{APP_ID}/install", json={})
        assert resp.status_code == 404

    def test_install_duplicate_returns_409(self):
        """すでにインストール済みの場合 409 を返す。"""
        mock_db = _mock_db_multi(
            [APP_ROW],          # app取得（published）
            [INSTALL_ROW],      # 既存インストールあり（is_active=True）
        )
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(f"/marketplace/apps/{APP_ID}/install", json={})
        assert resp.status_code == 409

    def test_uninstall_success(self):
        """インストール済みアプリをアンインストールできる（204）。"""
        mock_db = _mock_db_multi(
            [INSTALL_ROW],      # インストールレコード取得
            [INSTALL_ROW],      # update
        )
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().delete(f"/marketplace/apps/{APP_ID}/install")
        assert resp.status_code == 204

    def test_uninstall_not_installed_returns_404(self):
        """インストールされていない場合 404 を返す。"""
        mock_db = _mock_db([])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().delete(f"/marketplace/apps/{APP_ID}/install")
        assert resp.status_code == 404


# ===========================================================================
# test_review_app
# ===========================================================================

class TestReviewApp:
    def test_post_review_success(self):
        """インストール済みアプリにレビューを投稿できる（201）。"""
        mock_db = _mock_db_multi(
            [INSTALL_ROW],      # インストール確認
            [REVIEW_ROW],       # review insert
            [{"rating": 5}, {"rating": 4}],  # rating_avg 再計算用 select
            [APP_ROW],          # rating_avg update
        )
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(
                f"/marketplace/apps/{APP_ID}/review",
                json={"rating": 5, "comment": "とても使いやすいです"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["rating"] == 5
        assert body["comment"] == "とても使いやすいです"
        assert body["app_id"] == APP_ID

    def test_review_not_installed_returns_403(self):
        """インストールしていないアプリにはレビュー不可（403）。"""
        mock_db = _mock_db([])  # インストールなし
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(
                f"/marketplace/apps/{APP_ID}/review",
                json={"rating": 3},
            )
        assert resp.status_code == 403

    def test_review_invalid_rating_returns_422(self):
        """rating が 1〜5 の範囲外は 422 を返す。"""
        mock_db = _mock_db([INSTALL_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(
                f"/marketplace/apps/{APP_ID}/review",
                json={"rating": 6},
            )
        assert resp.status_code == 422

    def test_review_rating_below_minimum_returns_422(self):
        """rating が 0 以下は 422 を返す。"""
        mock_db = _mock_db([INSTALL_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client().post(
                f"/marketplace/apps/{APP_ID}/review",
                json={"rating": 0},
            )
        assert resp.status_code == 422


# ===========================================================================
# test_revenue_summary
# ===========================================================================

class TestRevenueSummary:
    def test_revenue_history_returns_monthly_summary(self):
        """収益サマリーが月次集計で返る。"""
        # 1回目: partner取得 / 2回目: revenue records
        mock_db = _mock_db_multi(
            [PARTNER_ROW],
            [REVENUE_ROW],
        )
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["partner_id"] == PARTNER_ID
        assert len(body["months"]) == 1
        m = body["months"][0]
        assert m["period_month"] == "2026-03"
        assert m["gross_amount_yen"] == 50000
        assert m["partner_amount_yen"] == 35000
        assert m["platform_amount_yen"] == 15000
        assert m["paid_count"] == 1

    def test_revenue_history_empty_when_no_records(self):
        """収益レコードがない場合は空リストを返す。"""
        mock_db = _mock_db_multi([PARTNER_ROW], [])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["months"] == []

    def test_revenue_history_unregistered_partner_returns_403(self):
        """パートナー未登録は 403 を返す。"""
        mock_db = _mock_db([])  # パートナーなし
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue")
        assert resp.status_code == 403

    def test_revenue_detail_success(self):
        """特定月の収益詳細が返る。"""
        mock_db = _mock_db_multi([PARTNER_ROW], [REVENUE_ROW])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue/2026-03")
        assert resp.status_code == 200
        body = resp.json()
        assert body["period_month"] == "2026-03"
        assert len(body["records"]) == 1
        assert body["summary"]["gross_amount_yen"] == 50000

    def test_revenue_detail_invalid_period_format_returns_422(self):
        """period_month が YYYY-MM 形式でない場合 422 を返す。"""
        mock_db = _mock_db_multi([PARTNER_ROW], [])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue/202603")
        assert resp.status_code == 422

    def test_revenue_multiple_months_aggregated(self):
        """複数月のレコードが正しく月ごとに集計される。"""
        row_jan = {**REVENUE_ROW, "period_month": "2026-01", "gross_amount_yen": 30000,
                   "partner_amount_yen": 21000, "platform_amount_yen": 9000, "status": "paid"}
        row_feb = {**REVENUE_ROW, "id": str(uuid4()), "period_month": "2026-02",
                   "gross_amount_yen": 50000, "partner_amount_yen": 35000,
                   "platform_amount_yen": 15000, "status": "pending"}
        mock_db = _mock_db_multi([PARTNER_ROW], [row_feb, row_jan])
        with patch("routers.partner.get_service_client", return_value=mock_db):
            resp = _make_client(PARTNER_USER).get("/partner/revenue")
        assert resp.status_code == 200
        body = resp.json()
        # 2ヶ月分が返る
        assert len(body["months"]) == 2
        periods = {m["period_month"] for m in body["months"]}
        assert periods == {"2026-01", "2026-02"}

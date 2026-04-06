"""収益分配エンジン（RevenueShareEngine）のユニットテスト。

全て Supabase・Stripe をモックし、DB 接続なしで実行する。
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.billing.revenue_share import RevenueShareEngine, RevenueShareSummary


# ─────────────────────────────────────
# テスト用フィクスチャ
# ─────────────────────────────────────

def _make_db_mock() -> MagicMock:
    """Supabase クライアントのチェーンモックを作る。"""
    db = MagicMock()
    # テーブル操作はメソッドチェーンで呼ばれるので全てMockで連鎖させる
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.in_.return_value = db
    db.upsert.return_value = db
    db.update.return_value = db
    db.maybe_single.return_value = db
    db.limit.return_value = db
    db.order.return_value = db
    return db


# ─────────────────────────────────────
# test_calculate_month_single_app
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_calculate_month_single_app():
    """1パートナー・1アプリ・1インストールの場合に収益が正しく計算される。"""
    db = _make_db_mock()

    # app_installations の結果
    db.execute.return_value = MagicMock(data=[
        {
            "id": "inst-001",
            "company_id": "co-001",
            "app_id": "app-001",
            "partner_apps": {
                "id": "app-001",
                "price_yen": 30000,
                "partner_id": "partner-001",
                "partners": {
                    "id": "partner-001",
                    "revenue_share_rate": 0.5,
                    "stripe_account_id": None,
                },
            },
        }
    ])

    with patch("workers.billing.revenue_share.get_service_client", return_value=db):
        engine = RevenueShareEngine()
        summaries = await engine.calculate_month("2026-04")

    assert len(summaries) == 1
    s = summaries[0]
    assert s.partner_id == "partner-001"
    assert s.period_month == "2026-04"
    assert s.total_gross_yen == 30000
    assert s.total_partner_yen == 15000   # 50%
    assert s.total_platform_yen == 15000  # 50%
    assert s.app_count == 1
    assert s.installation_count == 1
    assert s.status == "pending"
    assert s.stripe_payout_id is None


# ─────────────────────────────────────
# test_calculate_month_multiple_partners
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_calculate_month_multiple_partners():
    """2パートナー・それぞれ異なる revenue_share_rate で正しく分配される。"""
    db = _make_db_mock()

    db.execute.return_value = MagicMock(data=[
        # パートナーA: rate=0.6
        {
            "id": "inst-001",
            "company_id": "co-001",
            "app_id": "app-001",
            "partner_apps": {
                "id": "app-001",
                "price_yen": 10000,
                "partner_id": "partner-A",
                "partners": {
                    "id": "partner-A",
                    "revenue_share_rate": 0.6,
                    "stripe_account_id": None,
                },
            },
        },
        # パートナーB: rate=0.4
        {
            "id": "inst-002",
            "company_id": "co-002",
            "app_id": "app-002",
            "partner_apps": {
                "id": "app-002",
                "price_yen": 20000,
                "partner_id": "partner-B",
                "partners": {
                    "id": "partner-B",
                    "revenue_share_rate": 0.4,
                    "stripe_account_id": "acct_xxx",
                },
            },
        },
    ])

    with patch("workers.billing.revenue_share.get_service_client", return_value=db):
        engine = RevenueShareEngine()
        summaries = await engine.calculate_month("2026-04")

    assert len(summaries) == 2
    by_id = {s.partner_id: s for s in summaries}

    sA = by_id["partner-A"]
    assert sA.total_gross_yen == 10000
    assert sA.total_partner_yen == 6000   # 60%
    assert sA.total_platform_yen == 4000  # 40%

    sB = by_id["partner-B"]
    assert sB.total_gross_yen == 20000
    assert sB.total_partner_yen == 8000   # 40%
    assert sB.total_platform_yen == 12000 # 60%


# ─────────────────────────────────────
# test_payout_partner_stripe_unavailable_stays_pending
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_payout_partner_stripe_unavailable_stays_pending():
    """Stripe モジュールが None の場合、status=pending のまま処理が続く。"""
    db = _make_db_mock()

    # partners テーブル取得
    partner_resp = MagicMock(data={"id": "partner-001", "stripe_account_id": None})
    # revenue_share_records テーブル取得
    records_resp = MagicMock(data=[
        {"id": "rec-001", "partner_yen": 5000, "status": "pending"},
    ])

    # execute の呼び出し順に応じて異なる値を返す
    db.execute.side_effect = [partner_resp, records_resp]

    with patch("workers.billing.revenue_share.get_service_client", return_value=db):
        with patch("workers.billing.revenue_share.stripe", None):
            engine = RevenueShareEngine()
            result = await engine.payout_partner("partner-001", "2026-04")

    # stripe=None かつ stripe_account_id=None → pending:no_stripe_account_id
    assert result.startswith("pending:")


# ─────────────────────────────────────
# test_run_monthly_batch_with_error_continues
# ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_monthly_batch_with_error_continues():
    """あるパートナーで payout がエラーになっても他パートナーの処理が継続される。"""
    engine = RevenueShareEngine()

    # calculate_month は 2パートナーのサマリーを返す
    summary_a = RevenueShareSummary(
        partner_id="partner-A",
        period_month="2026-04",
        total_gross_yen=10000,
        total_partner_yen=5000,
        total_platform_yen=5000,
        app_count=1,
        installation_count=1,
        stripe_payout_id=None,
        status="pending",
    )
    summary_b = RevenueShareSummary(
        partner_id="partner-B",
        period_month="2026-04",
        total_gross_yen=20000,
        total_partner_yen=10000,
        total_platform_yen=10000,
        app_count=1,
        installation_count=1,
        stripe_payout_id=None,
        status="pending",
    )

    async def mock_calculate(period_month):
        return [summary_a, summary_b]

    call_count = 0

    async def mock_payout(partner_id, period_month):
        nonlocal call_count
        call_count += 1
        if partner_id == "partner-A":
            raise RuntimeError("Stripe エラー（テスト用）")
        # partner-B は成功
        return "tr_test_xxx"

    engine.calculate_month = mock_calculate
    engine.payout_partner = mock_payout

    result = await engine.run_monthly_batch("2026-04")

    # partner-A でエラーが発生しても partner-B は処理される
    assert call_count == 2
    assert result["processed_partners"] == 1   # partner-B のみ成功
    assert result["total_payout_yen"] == 10000  # partner-B の取り分
    assert len(result["errors"]) == 1           # partner-A のエラーが記録される
    assert "partner-A" in result["errors"][0]
    assert result["period_month"] == "2026-04"

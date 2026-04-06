"""ARPUスケール設計 — 使用量計測・請求サマリーエンドポイント（REQ-1503）。
スケール課金モデル（REQ-3003）: サブスクリプション管理・Stripe 連携エンドポイントを追加。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# 料金マスタ（CLAUDE.md 料金体系に準拠）
# ─────────────────────────────────────

_UNIT_PRICE_YEN: dict[str, int] = {
    "pipeline_run": 500,    # BPO実行 基本枠300回/月超の従量
    "connector_sync": 200,  # SaaS同期 従量
    "qa_query": 100,        # Q&A 基本枠500回/月超の従量
    "seat": 5000,           # 追加シート
}

# 月間基本枠（この枠内は従量課金対象外）
_FREE_QUOTA: dict[str, int] = {
    "pipeline_run": 300,
    "qa_query": 500,
    "connector_sync": 0,
    "seat": 0,
}


# ─────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────

class MetricTypeSummary(BaseModel):
    metric_type: str
    total_quantity: int
    free_quota: int
    billable_quantity: int
    unit_price_yen: int
    subtotal_yen: int


class UsageSummaryResponse(BaseModel):
    company_id: str
    period_month: str
    metrics: list[MetricTypeSummary]
    base_plan_yen: int           # 基本プラン月額（常に0を返す — 別テーブル管理）
    overage_yen: int             # 今月の従量課金額合計
    estimated_total_yen: int     # overage_yen（基本プランは別管理のため超過分のみ）
    generated_at: str


class MonthlyUsage(BaseModel):
    period_month: str
    total_quantity: int
    total_yen: int
    breakdown: dict[str, int]    # metric_type -> quantity


class UsageHistoryResponse(BaseModel):
    company_id: str
    months: list[MonthlyUsage]


class TrackUsageRequest(BaseModel):
    metric_type: str
    quantity: int = 1
    pipeline_name: Optional[str] = None
    period_month: Optional[str] = None   # 省略時は今月
    metadata: dict[str, Any] = {}


class TrackUsageResponse(BaseModel):
    id: str
    company_id: str
    metric_type: str
    quantity: int
    period_month: str
    created_at: str


# ─────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────

def _current_period_month() -> str:
    """現在の 'YYYY-MM' 文字列を返す。"""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _calc_summary(
    rows: list[dict[str, Any]],
    period_month: str,
    company_id: str,
) -> UsageSummaryResponse:
    """DBから取得した行リストからサマリーを計算する。"""
    # metric_type ごとに集計
    totals: dict[str, int] = {}
    for row in rows:
        mt = row["metric_type"]
        totals[mt] = totals.get(mt, 0) + row.get("quantity", 1)

    metrics: list[MetricTypeSummary] = []
    overage_yen = 0

    for metric_type, unit_price in _UNIT_PRICE_YEN.items():
        total_qty = totals.get(metric_type, 0)
        free_quota = _FREE_QUOTA.get(metric_type, 0)
        billable = max(0, total_qty - free_quota)
        subtotal = billable * unit_price
        overage_yen += subtotal

        metrics.append(MetricTypeSummary(
            metric_type=metric_type,
            total_quantity=total_qty,
            free_quota=free_quota,
            billable_quantity=billable,
            unit_price_yen=unit_price,
            subtotal_yen=subtotal,
        ))

    return UsageSummaryResponse(
        company_id=company_id,
        period_month=period_month,
        metrics=metrics,
        base_plan_yen=0,
        overage_yen=overage_yen,
        estimated_total_yen=overage_yen,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.get("/billing/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    period_month: Optional[str] = None,
    user: JWTClaims = Depends(get_current_user),
) -> UsageSummaryResponse:
    """今月（または指定月）の使用量サマリーと推定請求額を返す。

    - metric_type ごとの利用量・無料枠・従量課金額
    - 基本枠超過分の合計（overage_yen）
    """
    company_id = str(user.company_id)
    target_month = period_month or _current_period_month()

    try:
        db = get_service_client()
        result = (
            db.table("usage_metrics")
            .select("metric_type, quantity")
            .eq("company_id", company_id)
            .eq("period_month", target_month)
            .execute()
        )
        rows: list[dict[str, Any]] = result.data or []
    except Exception as e:
        logger.error(f"get_usage_summary DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return _calc_summary(rows, target_month, company_id)


@router.get("/billing/usage/history", response_model=UsageHistoryResponse)
async def get_usage_history(
    months: int = 6,
    user: JWTClaims = Depends(get_current_user),
) -> UsageHistoryResponse:
    """過去N ヶ月の月別使用量を返す（デフォルト6ヶ月）。"""
    company_id = str(user.company_id)
    months = min(max(1, months), 24)  # 1〜24ヶ月に制限

    try:
        db = get_service_client()
        result = (
            db.table("usage_metrics")
            .select("metric_type, quantity, period_month")
            .eq("company_id", company_id)
            .order("period_month", desc=True)
            .limit(months * 200)   # 月当たり最大200行を想定
            .execute()
        )
        rows: list[dict[str, Any]] = result.data or []
    except Exception as e:
        logger.error(f"get_usage_history DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    # period_month ごとに集計
    month_data: dict[str, dict[str, int]] = {}
    for row in rows:
        pm = row["period_month"]
        mt = row["metric_type"]
        qty = row.get("quantity", 1)
        if pm not in month_data:
            month_data[pm] = {}
        month_data[pm][mt] = month_data[pm].get(mt, 0) + qty

    monthly_list: list[MonthlyUsage] = []
    for pm in sorted(month_data.keys(), reverse=True)[:months]:
        breakdown = month_data[pm]
        total_qty = sum(breakdown.values())

        # 従量課金額を計算
        total_yen = 0
        for mt, qty in breakdown.items():
            free_quota = _FREE_QUOTA.get(mt, 0)
            unit_price = _UNIT_PRICE_YEN.get(mt, 0)
            billable = max(0, qty - free_quota)
            total_yen += billable * unit_price

        monthly_list.append(MonthlyUsage(
            period_month=pm,
            total_quantity=total_qty,
            total_yen=total_yen,
            breakdown=breakdown,
        ))

    return UsageHistoryResponse(company_id=company_id, months=monthly_list)


@router.post("/billing/usage/track", response_model=TrackUsageResponse)
async def track_usage(
    body: TrackUsageRequest,
    user: JWTClaims = Depends(require_role("admin", "editor")),
) -> TrackUsageResponse:
    """使用量を記録する（内部用 — パイプライン実行時に自動呼び出し）。

    - metric_type: 'pipeline_run' | 'connector_sync' | 'qa_query' | 'seat'
    - period_month 省略時は現在月（YYYY-MM）を自動セット
    """
    company_id = str(user.company_id)

    valid_types = {"pipeline_run", "connector_sync", "qa_query", "seat"}
    if body.metric_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"metric_type は {valid_types} のいずれかを指定してください",
        )

    if body.quantity < 1:
        raise HTTPException(status_code=422, detail="quantity は 1 以上を指定してください")

    period_month = body.period_month or _current_period_month()
    unit_price = _UNIT_PRICE_YEN.get(body.metric_type, 500)

    record: dict[str, Any] = {
        "company_id": company_id,
        "metric_type": body.metric_type,
        "quantity": body.quantity,
        "unit_price_yen": unit_price,
        "period_month": period_month,
        "metadata": body.metadata,
    }
    if body.pipeline_name:
        record["pipeline_name"] = body.pipeline_name

    try:
        db = get_service_client()
        result = db.table("usage_metrics").insert(record).execute()
        inserted = result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"track_usage 記録失敗: company={company_id[:8]} type={body.metric_type} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return TrackUsageResponse(
        id=inserted.get("id", ""),
        company_id=company_id,
        metric_type=body.metric_type,
        quantity=body.quantity,
        period_month=period_month,
        created_at=inserted.get("created_at", datetime.now(timezone.utc).isoformat()),
    )


# ═══════════════════════════════════════════════════════════════
# REQ-3003: スケール課金モデル — Stripe サブスクリプション統合
# ═══════════════════════════════════════════════════════════════

# ─────────────────────────────────────
# Pydantic モデル（Stripe 統合用）
# ─────────────────────────────────────

class SubscriptionResponse(BaseModel):
    """現在のサブスクリプション情報。"""
    id: str
    company_id: str
    plan: str
    status: str
    industry: Optional[str] = None
    onboarding_type: str
    onboarding_fee_yen: int
    stripe_subscription_id: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool
    created_at: str


class CheckoutSessionRequest(BaseModel):
    """Stripe Checkout セッション作成リクエスト。"""
    plan: str                           # common_bpo | industry_bpo | industry_bpo_support
    industry: Optional[str] = None      # 業種コード（industry_bpo 系の場合）
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(BaseModel):
    session_url: str


class BillingPortalRequest(BaseModel):
    """Stripe カスタマーポータルセッション作成リクエスト。"""
    return_url: str


class BillingPortalResponse(BaseModel):
    portal_url: str


class InvoiceItem(BaseModel):
    id: str
    amount_due_yen: int
    amount_paid_yen: int
    status: str
    period_start: Optional[int] = None   # Unix timestamp
    period_end: Optional[int] = None
    invoice_pdf: Optional[str] = None
    hosted_invoice_url: Optional[str] = None


class InvoicesResponse(BaseModel):
    company_id: str
    invoices: list[InvoiceItem]


# ─────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────

def _get_stripe_connector():
    """StripeBillingConnector をインポートして返す（遅延インポート）。"""
    from workers.connector.stripe_billing import StripeBillingConnector
    return StripeBillingConnector()


# ─────────────────────────────────────
# GET /billing/subscription
# ─────────────────────────────────────

@router.get("/billing/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    user: JWTClaims = Depends(require_role("admin")),
) -> SubscriptionResponse:
    """現在のサブスクリプション情報を返す。admin のみアクセス可。

    subscriptions テーブルから最新の有効なレコードを取得する。
    """
    company_id = str(user.company_id)

    try:
        db = get_service_client()
        result = (
            db.table("subscriptions")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows: list[dict[str, Any]] = result.data or []
    except Exception as e:
        logger.error(f"get_subscription DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="サブスクリプションが見つかりません。プランを選択してください。",
        )

    row = rows[0]
    return SubscriptionResponse(
        id=row["id"],
        company_id=row["company_id"],
        plan=row["plan"],
        status=row["status"],
        industry=row.get("industry"),
        onboarding_type=row.get("onboarding_type", "self"),
        onboarding_fee_yen=row.get("onboarding_fee_yen", 0),
        stripe_subscription_id=row.get("stripe_subscription_id"),
        stripe_customer_id=row.get("stripe_customer_id"),
        current_period_start=str(row["current_period_start"]) if row.get("current_period_start") else None,
        current_period_end=str(row["current_period_end"]) if row.get("current_period_end") else None,
        cancel_at_period_end=row.get("cancel_at_period_end", False),
        created_at=str(row["created_at"]),
    )


# ─────────────────────────────────────
# POST /billing/subscription/checkout
# ─────────────────────────────────────

@router.post("/billing/subscription/checkout", response_model=CheckoutSessionResponse)
async def create_checkout_session(
    body: CheckoutSessionRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> CheckoutSessionResponse:
    """Stripe Checkout セッションを作成してリダイレクト URL を返す。

    - plan: 'common_bpo' | 'industry_bpo' | 'industry_bpo_support'
    - 既存の stripe_customer_id があれば再利用、なければ新規作成
    """
    valid_plans = {"common_bpo", "industry_bpo", "industry_bpo_support"}
    if body.plan not in valid_plans:
        raise HTTPException(
            status_code=422,
            detail=f"plan は {valid_plans} のいずれかを指定してください",
        )

    company_id = str(user.company_id)

    # 既存の stripe_customer_id を取得（なければ作成）
    stripe_customer_id: Optional[str] = None
    try:
        db = get_service_client()
        result = (
            db.table("subscriptions")
            .select("stripe_customer_id")
            .eq("company_id", company_id)
            .not_.is_("stripe_customer_id", "null")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows:
            stripe_customer_id = rows[0].get("stripe_customer_id")
    except Exception as e:
        logger.warning(f"stripe_customer_id 取得失敗（続行）: company={company_id[:8]} error={e}")

    try:
        connector = _get_stripe_connector()

        # 顧客がいなければ作成（メールはユーザーのメールを使用）
        if not stripe_customer_id:
            stripe_customer_id = await connector.create_customer(
                company_id=company_id,
                email=user.email,
                name=company_id,  # 実際は companies テーブルから会社名を取得するのが理想
            )

        session_url = await connector.create_checkout_session(
            customer_id=stripe_customer_id,
            plan=body.plan,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            industry=body.industry,
        )
    except RuntimeError as e:
        # stripe ライブラリ未インストール
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"checkout_session 作成失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return CheckoutSessionResponse(session_url=session_url)


# ─────────────────────────────────────
# POST /billing/subscription/portal
# ─────────────────────────────────────

@router.post("/billing/subscription/portal", response_model=BillingPortalResponse)
async def create_billing_portal_session(
    body: BillingPortalRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> BillingPortalResponse:
    """Stripe カスタマーポータルセッションを作成して URL を返す。

    顧客が自分でプラン変更・解約・請求履歴を確認するためのポータル。
    stripe_customer_id が存在しない場合は 404 を返す。
    """
    company_id = str(user.company_id)

    # stripe_customer_id を取得
    try:
        db = get_service_client()
        result = (
            db.table("subscriptions")
            .select("stripe_customer_id")
            .eq("company_id", company_id)
            .not_.is_("stripe_customer_id", "null")
            .limit(1)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"portal_session DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not rows or not rows[0].get("stripe_customer_id"):
        raise HTTPException(
            status_code=404,
            detail="Stripe 顧客情報が見つかりません。先にプランを購入してください。",
        )

    stripe_customer_id = rows[0]["stripe_customer_id"]

    try:
        connector = _get_stripe_connector()
        portal_url = await connector.create_billing_portal_session(
            customer_id=stripe_customer_id,
            return_url=body.return_url,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"portal_session 作成失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return BillingPortalResponse(portal_url=portal_url)


# ─────────────────────────────────────
# POST /billing/webhook
# ─────────────────────────────────────

@router.post("/billing/webhook", status_code=200)
async def handle_stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
) -> dict[str, str]:
    """Stripe Webhook を受信して subscriptions テーブルを更新する。

    処理するイベント:
    - customer.subscription.updated  → plan/status/period を更新
    - customer.subscription.deleted  → status=canceled に更新
    - invoice.payment_failed         → status=past_due に更新

    署名検証失敗時は 400 を返す（Stripe が再送しないようにするため）。
    """
    payload = await request.body()
    sig_header = stripe_signature or ""

    try:
        connector = _get_stripe_connector()
        event = connector.handle_webhook(payload, sig_header)
    except ValueError as e:
        # 署名検証失敗
        logger.warning(f"Webhook 署名検証失敗: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Webhook 処理失敗: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    event_type: str = event.get("type", "")
    event_data: dict[str, Any] = event.get("data", {}).get("object", {})

    try:
        db = get_service_client()

        if event_type == "customer.subscription.updated":
            # サブスクリプション更新: plan/status/period/cancel_at_period_end を同期
            sub_id: str = event_data.get("id", "")
            new_status: str = event_data.get("status", "active")
            cancel_at_period_end: bool = event_data.get("cancel_at_period_end", False)
            period_start = event_data.get("current_period_start")
            period_end = event_data.get("current_period_end")

            # period は Unix timestamp → TIMESTAMPTZ に変換
            from datetime import datetime, timezone as tz
            period_start_dt = (
                datetime.fromtimestamp(period_start, tz=tz.utc).isoformat()
                if period_start else None
            )
            period_end_dt = (
                datetime.fromtimestamp(period_end, tz=tz.utc).isoformat()
                if period_end else None
            )

            update_payload: dict[str, Any] = {
                "status": new_status,
                "cancel_at_period_end": cancel_at_period_end,
            }
            if period_start_dt:
                update_payload["current_period_start"] = period_start_dt
            if period_end_dt:
                update_payload["current_period_end"] = period_end_dt

            db.table("subscriptions").update(update_payload).eq(
                "stripe_subscription_id", sub_id
            ).execute()
            logger.info(f"Webhook: subscription.updated sub_id={sub_id} status={new_status}")

        elif event_type == "customer.subscription.deleted":
            # サブスクリプション削除: canceled に更新
            sub_id = event_data.get("id", "")
            db.table("subscriptions").update({"status": "canceled"}).eq(
                "stripe_subscription_id", sub_id
            ).execute()
            logger.info(f"Webhook: subscription.deleted sub_id={sub_id}")

        elif event_type == "invoice.payment_failed":
            # 支払い失敗: past_due に更新
            customer_id: str = event_data.get("customer", "")
            db.table("subscriptions").update({"status": "past_due"}).eq(
                "stripe_customer_id", customer_id
            ).eq("status", "active").execute()
            logger.info(f"Webhook: invoice.payment_failed customer={customer_id}")

        else:
            logger.debug(f"Webhook: 未処理イベント type={event_type}")

    except Exception as e:
        logger.error(f"Webhook DB更新失敗: type={event_type} error={e}")
        # Stripe に 500 を返すと再送されるため、ここでは 200 を返す（ログのみ）

    return {"status": "ok"}


# ─────────────────────────────────────
# GET /billing/invoices
# ─────────────────────────────────────

@router.get("/billing/invoices", response_model=InvoicesResponse)
async def get_invoices(
    limit: int = 12,
    user: JWTClaims = Depends(require_role("admin")),
) -> InvoicesResponse:
    """Stripe から請求履歴を取得して返す。admin のみアクセス可。

    stripe_customer_id が存在しない場合は空リストを返す（エラーにしない）。
    """
    company_id = str(user.company_id)
    limit = min(max(1, limit), 100)

    # stripe_customer_id を取得
    stripe_customer_id: Optional[str] = None
    try:
        db = get_service_client()
        result = (
            db.table("subscriptions")
            .select("stripe_customer_id")
            .eq("company_id", company_id)
            .not_.is_("stripe_customer_id", "null")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows:
            stripe_customer_id = rows[0].get("stripe_customer_id")
    except Exception as e:
        logger.error(f"get_invoices DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not stripe_customer_id:
        return InvoicesResponse(company_id=company_id, invoices=[])

    try:
        connector = _get_stripe_connector()
        raw_invoices = await connector.list_invoices(
            customer_id=stripe_customer_id,
            limit=limit,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"get_invoices Stripe取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    invoices = [
        InvoiceItem(
            id=inv["id"],
            amount_due_yen=inv["amount_due_yen"],
            amount_paid_yen=inv["amount_paid_yen"],
            status=inv["status"],
            period_start=inv.get("period_start"),
            period_end=inv.get("period_end"),
            invoice_pdf=inv.get("invoice_pdf"),
            hosted_invoice_url=inv.get("hosted_invoice_url"),
        )
        for inv in raw_invoices
    ]

    return InvoicesResponse(company_id=company_id, invoices=invoices)


# ═══════════════════════════════════════════════════════════════
# 口座振替・手動請求（建設/製造の中小企業向け）
# ═══════════════════════════════════════════════════════════════

# ─────────────────────────────────────
# Pydantic モデル（手動請求用）
# ─────────────────────────────────────

class ManualInvoiceRequest(BaseModel):
    """手動請求書発行リクエスト。"""
    amount_yen: int
    description: str
    due_date: str                           # YYYY-MM-DD
    payment_method: str                     # "bank_transfer" | "invoice"
    bank_info: Optional[dict[str, Any]] = None   # 振込先情報（任意）


class ManualInvoiceResponse(BaseModel):
    """手動請求書レスポンス。"""
    invoice_id: str
    company_id: str
    amount_yen: int
    description: str
    due_date: str
    payment_method: str
    bank_info: Optional[dict[str, Any]] = None
    status: str
    paid_at: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str


# ─────────────────────────────────────
# POST /billing/invoices/manual
# ─────────────────────────────────────

@router.post("/billing/invoices/manual", response_model=ManualInvoiceResponse)
async def create_manual_invoice(
    body: ManualInvoiceRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> ManualInvoiceResponse:
    """手動請求書を発行する（admin のみ）。

    口座振替・請求書払いに対応。発行後 status='pending' で manual_invoices テーブルに保存する。

    - payment_method: 'bank_transfer' | 'invoice'
    - bank_info: 振込先口座情報（省略可）
    """
    valid_methods = {"bank_transfer", "invoice"}
    if body.payment_method not in valid_methods:
        raise HTTPException(
            status_code=422,
            detail=f"payment_method は {valid_methods} のいずれかを指定してください",
        )

    if body.amount_yen < 1:
        raise HTTPException(status_code=422, detail="amount_yen は 1 以上を指定してください")

    company_id = str(user.company_id)
    record: dict[str, Any] = {
        "company_id": company_id,
        "amount_yen": body.amount_yen,
        "description": body.description,
        "due_date": body.due_date,
        "payment_method": body.payment_method,
        "bank_info": body.bank_info,
        "status": "pending",
        "created_by": str(user.sub),
    }

    try:
        db = get_service_client()
        result = db.table("manual_invoices").insert(record).execute()
        inserted = result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"create_manual_invoice 記録失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ManualInvoiceResponse(
        invoice_id=inserted.get("id", ""),
        company_id=company_id,
        amount_yen=body.amount_yen,
        description=body.description,
        due_date=body.due_date,
        payment_method=body.payment_method,
        bank_info=body.bank_info,
        status="pending",
        paid_at=None,
        created_by=str(user.sub),
        created_at=inserted.get("created_at", datetime.now(timezone.utc).isoformat()),
    )


# ─────────────────────────────────────
# PATCH /billing/invoices/{invoice_id}/paid
# ─────────────────────────────────────

@router.patch("/billing/invoices/{invoice_id}/paid", response_model=ManualInvoiceResponse)
async def mark_manual_invoice_paid(
    invoice_id: str,
    user: JWTClaims = Depends(require_role("admin")),
) -> ManualInvoiceResponse:
    """入金確認後に請求書を支払済みにする（admin のみ）。

    - status を 'paid' に更新、paid_at を記録する
    - company_id フィルタで他テナントの請求書は更新できない
    """
    company_id = str(user.company_id)
    paid_at = datetime.now(timezone.utc).isoformat()

    try:
        db = get_service_client()
        # 対象レコードの存在確認（company_id でテナント分離）
        fetch_result = (
            db.table("manual_invoices")
            .select("*")
            .eq("id", invoice_id)
            .eq("company_id", company_id)
            .execute()
        )
        rows: list[dict[str, Any]] = fetch_result.data or []
    except Exception as e:
        logger.error(f"mark_manual_invoice_paid DB取得失敗: invoice={invoice_id} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not rows:
        raise HTTPException(status_code=404, detail="請求書が見つかりません")

    try:
        db = get_service_client()
        update_result = (
            db.table("manual_invoices")
            .update({"status": "paid", "paid_at": paid_at})
            .eq("id", invoice_id)
            .eq("company_id", company_id)
            .execute()
        )
        updated = update_result.data[0] if update_result.data else rows[0]
    except Exception as e:
        logger.error(f"mark_manual_invoice_paid DB更新失敗: invoice={invoice_id} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return ManualInvoiceResponse(
        invoice_id=updated.get("id", invoice_id),
        company_id=company_id,
        amount_yen=updated.get("amount_yen", rows[0].get("amount_yen", 0)),
        description=updated.get("description", rows[0].get("description", "")),
        due_date=str(updated.get("due_date", rows[0].get("due_date", ""))),
        payment_method=updated.get("payment_method", rows[0].get("payment_method", "bank_transfer")),
        bank_info=updated.get("bank_info", rows[0].get("bank_info")),
        status="paid",
        paid_at=paid_at,
        created_by=str(updated.get("created_by", rows[0].get("created_by", ""))),
        created_at=str(updated.get("created_at", rows[0].get("created_at", ""))),
    )


# ─────────────────────────────────────
# GET /billing/invoices/manual
# ─────────────────────────────────────

@router.get("/billing/invoices/manual", response_model=list[ManualInvoiceResponse])
async def list_manual_invoices(
    user: JWTClaims = Depends(get_current_user),
) -> list[ManualInvoiceResponse]:
    """手動請求書一覧を取得する（company_id でフィルタ、降順）。

    admin/editor どちらも参照可（発行は admin のみ）。
    """
    company_id = str(user.company_id)

    try:
        db = get_service_client()
        result = (
            db.table("manual_invoices")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows: list[dict[str, Any]] = result.data or []
    except Exception as e:
        logger.error(f"list_manual_invoices DB取得失敗: company={company_id[:8]} error={e}")
        raise HTTPException(status_code=500, detail=str(e))

    return [
        ManualInvoiceResponse(
            invoice_id=row["id"],
            company_id=row["company_id"],
            amount_yen=row["amount_yen"],
            description=row.get("description", ""),
            due_date=str(row.get("due_date", "")),
            payment_method=row.get("payment_method", "bank_transfer"),
            bank_info=row.get("bank_info"),
            status=row.get("status", "pending"),
            paid_at=str(row["paid_at"]) if row.get("paid_at") else None,
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]

"""Partner Marketplace — パートナー管理・アプリ公開・収益確認エンドポイント。

エンドポイント一覧:
  パートナー管理:
    POST   /partner/register            パートナー登録申請
    GET    /partner/me                  自社パートナー情報
    GET    /partner/apps                自社が作ったアプリ一覧
    POST   /partner/apps                アプリ新規作成（draft）
    PATCH  /partner/apps/{app_id}       アプリ更新
    POST   /partner/apps/{app_id}/publish  公開申請（status=review）
  Marketplace（全ユーザー）:
    GET    /marketplace/apps            公開アプリ一覧
    GET    /marketplace/apps/{app_id}   アプリ詳細
    POST   /marketplace/apps/{app_id}/install    インストール
    DELETE /marketplace/apps/{app_id}/install    アンインストール
    POST   /marketplace/apps/{app_id}/review     レビュー投稿
  収益確認:
    GET    /partner/revenue             月次収益サマリー（直近6ヶ月）
    GET    /partner/revenue/{period_month}  特定月の詳細
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth.middleware import get_current_user
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────
# 定数
# ─────────────────────────────────────

VALID_PARTNER_TYPES = {"sharoushi", "zeirishi", "gyoseishoshi", "bengoshi", "other"}
VALID_CATEGORIES = {"bpo", "template", "connector"}
VALID_PRICING_MODELS = {"monthly", "one_time", "free"}
VALID_APP_STATUSES = {"draft", "review", "published", "unpublished"}

# 直近何ヶ月分の収益を返すか
REVENUE_MONTHS_DEFAULT = 6


# ─────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────

class PartnerRegisterRequest(BaseModel):
    display_name: str
    partner_type: str = "sharoushi"
    contact_email: Optional[str] = None


class PartnerResponse(BaseModel):
    id: UUID
    company_id: UUID
    display_name: str
    partner_type: str
    contact_email: Optional[str]
    revenue_share_rate: float
    is_approved: bool
    approved_at: Optional[datetime]
    created_at: datetime


class AppCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    category: str                               # bpo | template | connector
    price_yen: int = Field(default=0, ge=0)
    pricing_model: str = "monthly"
    genome_config: Optional[dict[str, Any]] = None
    pipeline_config: Optional[dict[str, Any]] = None
    icon_url: Optional[str] = None


class AppUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    price_yen: Optional[int] = Field(default=None, ge=0)
    pricing_model: Optional[str] = None
    genome_config: Optional[dict[str, Any]] = None
    pipeline_config: Optional[dict[str, Any]] = None
    icon_url: Optional[str] = None


class AppResponse(BaseModel):
    id: UUID
    partner_id: UUID
    name: str
    description: Optional[str]
    category: str
    price_yen: int
    pricing_model: str
    genome_config: Optional[dict[str, Any]]
    pipeline_config: Optional[dict[str, Any]]
    icon_url: Optional[str]
    status: str
    install_count: int
    rating_avg: Optional[float]
    created_at: datetime
    updated_at: datetime


class AppListResponse(BaseModel):
    items: list[AppResponse]
    total: int


class InstallRequest(BaseModel):
    config: Optional[dict[str, Any]] = None    # 会社ごとのカスタム設定


class InstallResponse(BaseModel):
    id: UUID
    app_id: UUID
    company_id: UUID
    installed_at: datetime
    config: Optional[dict[str, Any]]
    is_active: bool


class ReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class ReviewResponse(BaseModel):
    id: UUID
    app_id: UUID
    company_id: UUID
    reviewer_user_id: Optional[UUID]
    rating: int
    comment: Optional[str]
    created_at: datetime


class RevenueRecord(BaseModel):
    id: UUID
    app_id: UUID
    company_id: UUID
    period_month: str
    gross_amount_yen: int
    partner_amount_yen: int
    platform_amount_yen: int
    revenue_share_rate: Optional[float]
    stripe_payout_id: Optional[str]
    status: str
    paid_at: Optional[datetime]
    created_at: datetime


class RevenueMonthlySummary(BaseModel):
    period_month: str
    gross_amount_yen: int
    partner_amount_yen: int
    platform_amount_yen: int
    record_count: int
    paid_count: int


class RevenueHistoryResponse(BaseModel):
    partner_id: UUID
    months: list[RevenueMonthlySummary]


class RevenueDetailResponse(BaseModel):
    partner_id: UUID
    period_month: str
    records: list[RevenueRecord]
    summary: RevenueMonthlySummary


# ─────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────

def _get_partner_for_company(db: Any, company_id: str) -> dict[str, Any] | None:
    """自社のパートナーレコードを1件取得する。なければ None。"""
    res = (
        db.table("partners")
        .select("*")
        .eq("company_id", company_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _require_approved_partner(db: Any, company_id: str) -> dict[str, Any]:
    """承認済みパートナーレコードを返す。未登録・未承認は 403。"""
    partner = _get_partner_for_company(db, company_id)
    if not partner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="パートナー登録が必要です。POST /partner/register を呼んでください。",
        )
    if not partner.get("is_approved"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="パートナー審査が完了していません。承認後に操作できます。",
        )
    return partner


def _get_app_owned_by_partner(db: Any, app_id: str, partner_id: str) -> dict[str, Any]:
    """partner_id が所有する app を取得。なければ 404。"""
    res = (
        db.table("partner_apps")
        .select("*")
        .eq("id", app_id)
        .eq("partner_id", partner_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="アプリが見つかりません。",
        )
    return rows[0]


# ─────────────────────────────────────
# パートナー管理
# ─────────────────────────────────────

@router.post("/partner/register", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED)
async def register_partner(
    body: PartnerRegisterRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """パートナー登録申請。1社につき1パートナーレコードのみ。"""
    if body.partner_type not in VALID_PARTNER_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"partner_type は {VALID_PARTNER_TYPES} のいずれかを指定してください。",
        )

    db = get_service_client()

    # 重複チェック
    existing = _get_partner_for_company(db, user.company_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="すでにパートナー登録済みです。",
        )

    payload = {
        "company_id": user.company_id,
        "display_name": body.display_name,
        "partner_type": body.partner_type,
        "contact_email": body.contact_email,
    }
    res = db.table("partners").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="パートナー登録に失敗しました。",
        )
    return rows[0]


@router.get("/partner/me", response_model=PartnerResponse)
async def get_my_partner(user: JWTClaims = Depends(get_current_user)):
    """自社のパートナー情報を返す。"""
    db = get_service_client()
    partner = _get_partner_for_company(db, user.company_id)
    if not partner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="パートナー登録が見つかりません。",
        )
    return partner


@router.get("/partner/apps", response_model=AppListResponse)
async def list_my_apps(user: JWTClaims = Depends(get_current_user)):
    """自社が作ったアプリ一覧（status 問わず全件）。"""
    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)

    res = (
        db.table("partner_apps")
        .select("*")
        .eq("partner_id", partner["id"])
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    return AppListResponse(items=rows, total=len(rows))


@router.post("/partner/apps", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
async def create_app(
    body: AppCreateRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """アプリ新規作成（status=draft で登録）。"""
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"category は {VALID_CATEGORIES} のいずれかを指定してください。",
        )
    if body.pricing_model not in VALID_PRICING_MODELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"pricing_model は {VALID_PRICING_MODELS} のいずれかを指定してください。",
        )

    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)

    payload = {
        "partner_id": partner["id"],
        "name": body.name,
        "description": body.description,
        "category": body.category,
        "price_yen": body.price_yen,
        "pricing_model": body.pricing_model,
        "genome_config": body.genome_config,
        "pipeline_config": body.pipeline_config,
        "icon_url": body.icon_url,
        "status": "draft",
    }
    res = db.table("partner_apps").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="アプリ作成に失敗しました。",
        )
    return rows[0]


@router.patch("/partner/apps/{app_id}", response_model=AppResponse)
async def update_app(
    app_id: UUID,
    body: AppUpdateRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """アプリ更新。published 状態でも更新可（再審査は /publish で）。"""
    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)
    _get_app_owned_by_partner(db, str(app_id), partner["id"])  # 存在・所有確認

    # 更新フィールドのみ抽出
    updates: dict[str, Any] = {}
    for field, val in body.model_dump(exclude_unset=True).items():
        if field == "category" and val not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"category は {VALID_CATEGORIES} のいずれかを指定してください。",
            )
        if field == "pricing_model" and val not in VALID_PRICING_MODELS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"pricing_model は {VALID_PRICING_MODELS} のいずれかを指定してください。",
            )
        updates[field] = val

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="更新するフィールドがありません。",
        )

    res = (
        db.table("partner_apps")
        .update(updates)
        .eq("id", str(app_id))
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="アプリ更新に失敗しました。",
        )
    return rows[0]


@router.post("/partner/apps/{app_id}/publish", response_model=AppResponse)
async def publish_app(
    app_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """公開申請（status: draft/unpublished → review）。
    管理者が is_approved を true にした後、実際の公開は管理画面で行う。
    """
    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)
    app = _get_app_owned_by_partner(db, str(app_id), partner["id"])

    if app["status"] == "review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="すでに審査中です。",
        )
    if app["status"] == "published":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="すでに公開済みです。",
        )

    res = (
        db.table("partner_apps")
        .update({"status": "review"})
        .eq("id", str(app_id))
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="公開申請に失敗しました。",
        )
    return rows[0]


# ─────────────────────────────────────
# Marketplace（全ユーザー）
# ─────────────────────────────────────

@router.get("/marketplace/apps", response_model=AppListResponse)
async def list_marketplace_apps(
    category: Optional[str] = Query(default=None, description="bpo | template | connector"),
    max_price_yen: Optional[int] = Query(default=None, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """公開アプリ一覧（status=published のみ）。category/price でフィルタ可。"""
    db = get_service_client()

    chain = db.table("partner_apps").select("*").eq("status", "published")

    if category:
        if category not in VALID_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"category は {VALID_CATEGORIES} のいずれかを指定してください。",
            )
        chain = chain.eq("category", category)

    if max_price_yen is not None:
        chain = chain.lte("price_yen", max_price_yen)

    res = chain.order("install_count", desc=True).execute()
    rows = res.data or []
    return AppListResponse(items=rows, total=len(rows))


@router.get("/marketplace/apps/{app_id}", response_model=AppResponse)
async def get_marketplace_app(
    app_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """アプリ詳細（公開済みのみ）。"""
    db = get_service_client()
    res = (
        db.table("partner_apps")
        .select("*")
        .eq("id", str(app_id))
        .eq("status", "published")
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="アプリが見つかりません。",
        )
    return rows[0]


@router.post(
    "/marketplace/apps/{app_id}/install",
    response_model=InstallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_app(
    app_id: UUID,
    body: InstallRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """アプリをインストールする。published のみインストール可。"""
    db = get_service_client()

    # アプリが公開済みか確認
    app_res = (
        db.table("partner_apps")
        .select("id, status")
        .eq("id", str(app_id))
        .limit(1)
        .execute()
    )
    app_rows = app_res.data or []
    if not app_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="アプリが見つかりません。",
        )
    if app_rows[0]["status"] != "published":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="公開済みアプリのみインストールできます。",
        )

    # 重複インストールチェック
    existing_res = (
        db.table("app_installations")
        .select("id, is_active")
        .eq("app_id", str(app_id))
        .eq("company_id", user.company_id)
        .limit(1)
        .execute()
    )
    existing_rows = existing_res.data or []
    if existing_rows and existing_rows[0].get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="すでにインストール済みです。",
        )

    if existing_rows and not existing_rows[0].get("is_active"):
        # 以前アンインストールしていた場合は再有効化
        res = (
            db.table("app_installations")
            .update({"is_active": True, "config": body.config})
            .eq("id", existing_rows[0]["id"])
            .execute()
        )
        rows = res.data or []
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="インストールに失敗しました。",
            )
        # install_count 更新（increment は DB 側トリガー推奨だが MVP では手動）
        db.table("partner_apps").update(
            {"install_count": app_rows[0].get("install_count", 0) + 1}
        ).eq("id", str(app_id)).execute()
        return rows[0]

    # 新規インストール
    payload = {
        "app_id": str(app_id),
        "company_id": user.company_id,
        "config": body.config,
    }
    res = db.table("app_installations").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="インストールに失敗しました。",
        )
    # install_count インクリメント
    db.table("partner_apps").update(
        {"install_count": app_rows[0].get("install_count", 0) + 1}
    ).eq("id", str(app_id)).execute()
    return rows[0]


@router.delete("/marketplace/apps/{app_id}/install", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_app(
    app_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """アプリをアンインストール（is_active=false に更新）。"""
    db = get_service_client()

    res = (
        db.table("app_installations")
        .select("id, is_active")
        .eq("app_id", str(app_id))
        .eq("company_id", user.company_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows or not rows[0].get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="インストール済みのアプリが見つかりません。",
        )

    db.table("app_installations").update({"is_active": False}).eq("id", rows[0]["id"]).execute()


@router.post(
    "/marketplace/apps/{app_id}/review",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_review(
    app_id: UUID,
    body: ReviewRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """レビュー投稿。1社につき1レビューのみ（UNIQUE制約）。インストール済みのみ投稿可。"""
    db = get_service_client()

    # インストール済みかチェック
    install_res = (
        db.table("app_installations")
        .select("id")
        .eq("app_id", str(app_id))
        .eq("company_id", user.company_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not (install_res.data or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="インストール済みのアプリにのみレビューを投稿できます。",
        )

    payload = {
        "app_id": str(app_id),
        "company_id": user.company_id,
        "reviewer_user_id": user.sub,
        "rating": body.rating,
        "comment": body.comment,
    }
    res = db.table("app_reviews").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="レビュー投稿に失敗しました。（すでにレビュー済みの可能性があります）",
        )

    # rating_avg を再計算して partner_apps を更新
    avg_res = (
        db.table("app_reviews")
        .select("rating")
        .eq("app_id", str(app_id))
        .execute()
    )
    avg_rows = avg_res.data or []
    if avg_rows:
        avg = sum(r["rating"] for r in avg_rows) / len(avg_rows)
        db.table("partner_apps").update({"rating_avg": round(avg, 2)}).eq("id", str(app_id)).execute()

    return rows[0]


# ─────────────────────────────────────
# 収益確認
# ─────────────────────────────────────

@router.get("/partner/revenue", response_model=RevenueHistoryResponse)
async def get_revenue_history(
    months: int = Query(default=REVENUE_MONTHS_DEFAULT, ge=1, le=24),
    user: JWTClaims = Depends(get_current_user),
):
    """月次収益サマリー（直近 N ヶ月、デフォルト6ヶ月）。"""
    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)

    res = (
        db.table("revenue_share_records")
        .select("*")
        .eq("partner_id", partner["id"])
        .order("period_month", desc=True)
        .limit(months * 50)   # 1ヶ月あたり最大50件想定（十分なバッファ）
        .execute()
    )
    rows: list[dict[str, Any]] = res.data or []

    # period_month ごとに集計
    monthly: dict[str, dict[str, Any]] = {}
    for row in rows:
        pm = row["period_month"]
        if pm not in monthly:
            monthly[pm] = {
                "period_month": pm,
                "gross_amount_yen": 0,
                "partner_amount_yen": 0,
                "platform_amount_yen": 0,
                "record_count": 0,
                "paid_count": 0,
            }
        m = monthly[pm]
        m["gross_amount_yen"] += row.get("gross_amount_yen", 0)
        m["partner_amount_yen"] += row.get("partner_amount_yen", 0)
        m["platform_amount_yen"] += row.get("platform_amount_yen", 0)
        m["record_count"] += 1
        if row.get("status") == "paid":
            m["paid_count"] += 1

    # 最新N件のみ返す（period_month降順）
    sorted_months = sorted(monthly.values(), key=lambda x: x["period_month"], reverse=True)[:months]

    return RevenueHistoryResponse(
        partner_id=UUID(partner["id"]),
        months=[RevenueMonthlySummary(**m) for m in sorted_months],
    )


@router.post("/partner/revenue/batch/{period_month}", tags=["partner"])
async def run_revenue_batch(
    period_month: str,
    user: JWTClaims = Depends(get_current_user),
):
    """月次収益バッチを手動実行する（admin 専用）。

    指定月の全パートナー収益を計算し、Stripe Connect で送金する。
    period_month: YYYY-MM 形式
    """
    import re
    if not re.match(r"^\d{4}-\d{2}$", period_month):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_month は YYYY-MM 形式で指定してください。",
        )

    if getattr(user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="このエンドポイントは admin ロールのみ実行できます。",
        )

    from workers.billing.revenue_share import RevenueShareEngine  # noqa: PLC0415
    engine = RevenueShareEngine()
    result = await engine.run_monthly_batch(period_month)
    logger.info("revenue batch triggered by user=%s result=%s", user.sub, result)
    return result


@router.get("/partner/revenue/{period_month}", response_model=RevenueDetailResponse)
async def get_revenue_detail(
    period_month: str,
    user: JWTClaims = Depends(get_current_user),
):
    """特定月の収益詳細（YYYY-MM 形式）。"""
    # YYYY-MM フォーマット簡易チェック
    import re
    if not re.match(r"^\d{4}-\d{2}$", period_month):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_month は YYYY-MM 形式で指定してください。",
        )

    db = get_service_client()
    partner = _require_approved_partner(db, user.company_id)

    res = (
        db.table("revenue_share_records")
        .select("*")
        .eq("partner_id", partner["id"])
        .eq("period_month", period_month)
        .order("created_at", desc=False)
        .execute()
    )
    rows: list[dict[str, Any]] = res.data or []

    # サマリー計算
    summary = RevenueMonthlySummary(
        period_month=period_month,
        gross_amount_yen=sum(r.get("gross_amount_yen", 0) for r in rows),
        partner_amount_yen=sum(r.get("partner_amount_yen", 0) for r in rows),
        platform_amount_yen=sum(r.get("platform_amount_yen", 0) for r in rows),
        record_count=len(rows),
        paid_count=sum(1 for r in rows if r.get("status") == "paid"),
    )

    return RevenueDetailResponse(
        partner_id=UUID(partner["id"]),
        period_month=period_month,
        records=[RevenueRecord(**r) for r in rows],
        summary=summary,
    )

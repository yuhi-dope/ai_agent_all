"""CRM（Customer Relationship Management）エンドポイント — 顧客管理・売上・要望管理"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models — Customer
# ---------------------------------------------------------------------------


class CustomerUpdate(BaseModel):
    plan: Optional[str] = None              # brain / bpo_core / enterprise
    active_modules: Optional[list[str]] = None
    mrr: Optional[int] = None
    health_score: Optional[int] = None
    nps_score: Optional[int] = None
    status: Optional[str] = None            # onboarding / active / at_risk / churned
    cs_owner: Optional[UUID] = None
    churn_reason: Optional[str] = None
    version: int                            # 楽観的ロック用


class CustomerResponse(BaseModel):
    id: UUID
    lead_id: Optional[UUID] = None
    customer_company_name: str
    industry: str
    employee_count: Optional[int] = None
    plan: str
    active_modules: list
    mrr: int
    health_score: Optional[int] = None
    nps_score: Optional[int] = None
    last_nps_at: Optional[datetime] = None
    status: str
    onboarded_at: Optional[datetime] = None
    churned_at: Optional[datetime] = None
    churn_reason: Optional[str] = None
    cs_owner: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class CustomerListResponse(BaseModel):
    items: list[CustomerResponse]
    total: int
    has_more: bool = False


class HealthScoreHistory(BaseModel):
    id: UUID
    score: int
    dimensions: dict                        # {usage, engagement, support, nps, expansion}
    risk_factors: list
    calculated_at: datetime


class CustomerHealthResponse(BaseModel):
    customer_id: UUID
    current_score: Optional[int] = None
    history: list[HealthScoreHistory]


class TimelineEvent(BaseModel):
    event_type: str                         # ticket / health_update / nps / contract / feature_request
    title: str
    description: Optional[str] = None
    occurred_at: datetime


class CustomerTimelineResponse(BaseModel):
    customer_id: UUID
    events: list[TimelineEvent]


# ---------------------------------------------------------------------------
# Request / Response models — Revenue
# ---------------------------------------------------------------------------


class RevenueSummaryResponse(BaseModel):
    mrr: int                                # 月次経常収益（円）
    arr: int                                # 年次経常収益
    nrr: float                              # Net Revenue Retention（%）
    churn_rate: float                       # 月次チャーン率（%）
    active_customer_count: int
    churned_this_month: int
    expansion_mrr: int                      # 拡張MRR
    contraction_mrr: int                    # 縮小MRR
    new_mrr: int                            # 新規MRR
    updated_at: datetime


class RevenueMonthlyItem(BaseModel):
    month: str                              # YYYY-MM
    mrr: int
    new_mrr: int
    expansion_mrr: int
    contraction_mrr: int
    churn_mrr: int
    net_change: int


class RevenueMonthlyResponse(BaseModel):
    items: list[RevenueMonthlyItem]
    months: int


class CohortItem(BaseModel):
    cohort_month: str                       # 契約開始月（YYYY-MM）
    initial_mrr: int
    months: list[dict]                      # [{month_offset, retention_rate, mrr}]


class RevenueCohortResponse(BaseModel):
    cohorts: list[CohortItem]


# ---------------------------------------------------------------------------
# Request / Response models — Feature Request
# ---------------------------------------------------------------------------


class FeatureRequestCreate(BaseModel):
    customer_id: UUID
    title: str
    description: str
    category: Optional[str] = None          # feature / improvement / integration / bug
    priority: Optional[str] = "medium"      # low / medium / high / critical


class FeatureRequestUpdate(BaseModel):
    status: Optional[str] = None            # new/reviewing/planned/in_progress/done/declined
    priority: Optional[str] = None
    response: Optional[str] = None
    version: int                            # 楽観的ロック用


class FeatureRequestResponse(BaseModel):
    id: UUID
    customer_id: UUID
    title: str
    description: str
    category: Optional[str] = None
    priority: str
    ai_category: Optional[dict] = None
    similar_request_ids: Optional[list[UUID]] = None
    vote_count: int
    status: str
    response: Optional[str] = None
    responded_at: Optional[datetime] = None
    created_at: datetime


class FeatureRequestListResponse(BaseModel):
    items: list[FeatureRequestResponse]
    total: int
    has_more: bool = False


class FeatureRequestRankingItem(BaseModel):
    id: UUID
    title: str
    category: Optional[str] = None
    priority: str
    vote_count: int
    impacted_mrr: int                       # 要望を持つ顧客の合計MRR
    status: str


class FeatureRequestRankingResponse(BaseModel):
    items: list[FeatureRequestRankingItem]
    total: int


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

# customers テーブルには version カラムが存在しないため、
# 楽観的ロックは updated_at の比較でエミュレートする。
# リクエストの version フィールドは updated_at の Unix 秒として扱う。
# ただし、既存モデルとの互換性のため version=0 は常に許可する（初回更新）。

_CUSTOMERS_SELECT = (
    "id, lead_id, customer_company_name, industry, employee_count, plan, "
    "active_modules, mrr, health_score, nps_score, last_nps_at, status, "
    "onboarded_at, churned_at, churn_reason, cs_owner, created_at, updated_at"
)


def _build_customer_response(row: dict) -> CustomerResponse:
    return CustomerResponse(
        id=row["id"],
        lead_id=row.get("lead_id"),
        customer_company_name=row["customer_company_name"],
        industry=row["industry"],
        employee_count=row.get("employee_count"),
        plan=row["plan"],
        active_modules=row.get("active_modules") or [],
        mrr=row.get("mrr") or 0,
        health_score=row.get("health_score"),
        nps_score=row.get("nps_score"),
        last_nps_at=row.get("last_nps_at"),
        status=row["status"],
        onboarded_at=row.get("onboarded_at"),
        churned_at=row.get("churned_at"),
        churn_reason=row.get("churn_reason"),
        cs_owner=row.get("cs_owner"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Endpoints — Customer
# ---------------------------------------------------------------------------


@router.get("/crm/customers", response_model=CustomerListResponse)
async def list_customers(
    customer_status: Optional[str] = Query(None, alias="status"),
    min_health_score: Optional[int] = None,
    plan: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """顧客一覧を取得する（ヘルススコア降順）。

    - customers テーブルから取得
    - status / health_score / plan でフィルタ可能
    """
    try:
        db = get_service_client()
        q = (
            db.table("customers")
            .select(_CUSTOMERS_SELECT, count="exact")
            .eq("company_id", str(user.company_id))
            .order("health_score", desc=True)
            .range(offset, offset + limit - 1)
        )
        if customer_status:
            q = q.eq("status", customer_status)
        if plan:
            q = q.eq("plan", plan)
        if min_health_score is not None:
            q = q.gte("health_score", min_health_score)

        result = q.execute()
        items = [_build_customer_response(r) for r in (result.data or [])]
        total = result.count or 0
        return CustomerListResponse(
            items=items,
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list customers failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/customers/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """顧客詳細を取得する（360度ビュー）。"""
    try:
        db = get_service_client()
        result = (
            db.table("customers")
            .select(_CUSTOMERS_SELECT)
            .eq("id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        return _build_customer_response(result.data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get customer failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/customers/{customer_id}/health", response_model=CustomerHealthResponse)
async def get_customer_health(
    customer_id: UUID,
    limit: int = Query(30, ge=1, le=365, description="取得する履歴件数"),
    user: JWTClaims = Depends(get_current_user),
):
    """顧客ヘルススコア履歴を取得する。"""
    try:
        db = get_service_client()

        # 顧客の存在確認（テナント分離）
        cust_result = (
            db.table("customers")
            .select("id, health_score")
            .eq("id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not cust_result.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        # ヘルス履歴を取得
        health_result = (
            db.table("customer_health")
            .select("id, score, dimensions, risk_factors, calculated_at")
            .eq("customer_id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .order("calculated_at", desc=True)
            .limit(limit)
            .execute()
        )

        history = [
            HealthScoreHistory(
                id=r["id"],
                score=r["score"],
                dimensions=r.get("dimensions") or {},
                risk_factors=r.get("risk_factors") or [],
                calculated_at=r["calculated_at"],
            )
            for r in (health_result.data or [])
        ]

        return CustomerHealthResponse(
            customer_id=customer_id,
            current_score=cust_result.data.get("health_score"),
            history=history,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get customer health failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/customers/{customer_id}/timeline", response_model=CustomerTimelineResponse)
async def get_customer_timeline(
    customer_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    user: JWTClaims = Depends(get_current_user),
):
    """顧客のアクティビティタイムラインを取得する。

    support_tickets / customer_health / feature_requests / lead_activities を
    時系列に統合して返す。
    """
    try:
        db = get_service_client()

        # 顧客の存在確認（lead_id も取得）
        cust_result = (
            db.table("customers")
            .select("id, lead_id")
            .eq("id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not cust_result.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        events: list[TimelineEvent] = []

        # サポートチケット
        tickets_result = (
            db.table("support_tickets")
            .select("id, subject, status, priority, created_at")
            .eq("customer_id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        for r in (tickets_result.data or []):
            events.append(TimelineEvent(
                event_type="ticket",
                title=f"[チケット] {r.get('subject', '（件名なし）')}",
                description=f"status={r.get('status')} priority={r.get('priority')}",
                occurred_at=r["created_at"],
            ))

        # ヘルススコア更新履歴
        health_result = (
            db.table("customer_health")
            .select("id, score, risk_factors, calculated_at")
            .eq("customer_id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .order("calculated_at", desc=True)
            .limit(limit)
            .execute()
        )
        for r in (health_result.data or []):
            risk_factors = r.get("risk_factors") or []
            desc = f"score={r.get('score')}"
            if risk_factors:
                desc += f" risks={risk_factors[:2]}"
            events.append(TimelineEvent(
                event_type="health_update",
                title=f"[ヘルス更新] スコア {r.get('score')}",
                description=desc,
                occurred_at=r["calculated_at"],
            ))

        # 要望
        fr_result = (
            db.table("feature_requests")
            .select("id, title, status, created_at")
            .eq("customer_id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        for r in (fr_result.data or []):
            events.append(TimelineEvent(
                event_type="feature_request",
                title=f"[要望] {r.get('title', '（タイトルなし）')}",
                description=f"status={r.get('status')}",
                occurred_at=r["created_at"],
            ))

        # リードのアクティビティ（lead_id がある場合のみ）
        lead_id = cust_result.data.get("lead_id")
        if lead_id:
            la_result = (
                db.table("lead_activities")
                .select("id, activity_type, activity_data, channel, created_at")
                .eq("lead_id", str(lead_id))
                .eq("company_id", str(user.company_id))
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            for r in (la_result.data or []):
                events.append(TimelineEvent(
                    event_type="lead_activity",
                    title=f"[活動] {r.get('activity_type', '')}",
                    description=f"channel={r.get('channel')}",
                    occurred_at=r["created_at"],
                ))

        # 時系列降順ソートしてlimitを適用
        events.sort(key=lambda e: str(e.occurred_at), reverse=True)
        events = events[:limit]

        return CustomerTimelineResponse(customer_id=customer_id, events=events)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get customer timeline failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/crm/customers/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: UUID,
    body: CustomerUpdate,
    user: JWTClaims = Depends(get_current_user),
):
    """顧客情報を更新する（楽観的ロック）。

    customers テーブルには version カラムが存在しないため、
    version=0 の場合は無条件更新、それ以外の場合は version を
    updated_at の epoch 秒（整数）として扱い不一致時に 409 を返す。
    """
    try:
        db = get_service_client()

        # 現在レコード取得
        current = (
            db.table("customers")
            .select("id, updated_at")
            .eq("id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not current.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        # 楽観的ロック（version > 0 の場合のみチェック）
        if body.version > 0:
            updated_at_str = current.data.get("updated_at", "")
            try:
                # Supabase は ISO 8601 文字列を返すので epoch 秒に変換
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                current_version = int(dt.timestamp())
            except Exception:
                current_version = 0
            if current_version != body.version:
                raise HTTPException(
                    status_code=409,
                    detail="VERSION_CONFLICT: Customer has been modified by another user. Please refresh and try again.",
                )

        # 更新ペイロード構築
        update_data: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if body.plan is not None:
            update_data["plan"] = body.plan
        if body.active_modules is not None:
            update_data["active_modules"] = body.active_modules
        if body.mrr is not None:
            update_data["mrr"] = body.mrr
        if body.health_score is not None:
            update_data["health_score"] = body.health_score
        if body.nps_score is not None:
            update_data["nps_score"] = body.nps_score
            update_data["last_nps_at"] = datetime.now(timezone.utc).isoformat()
        if body.status is not None:
            update_data["status"] = body.status
            if body.status == "churned" and not body.churn_reason:
                update_data["churned_at"] = datetime.now(timezone.utc).isoformat()
        if body.cs_owner is not None:
            update_data["cs_owner"] = str(body.cs_owner)
        if body.churn_reason is not None:
            update_data["churn_reason"] = body.churn_reason

        result = (
            db.table("customers")
            .update(update_data)
            .eq("id", str(customer_id))
            .eq("company_id", str(user.company_id))
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=500, detail="Update failed")

        return _build_customer_response(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update customer failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Revenue
# ---------------------------------------------------------------------------


@router.get("/crm/revenue/summary", response_model=RevenueSummaryResponse)
async def get_revenue_summary(
    user: JWTClaims = Depends(get_current_user),
):
    """MRR/ARR/NRR サマリーを取得する。

    customers テーブルと revenue_records テーブルから集計する。
    """
    try:
        db = get_service_client()
        now = datetime.now(timezone.utc)
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # アクティブ顧客のMRR集計
        customers_result = (
            db.table("customers")
            .select("mrr, status, churned_at")
            .eq("company_id", str(user.company_id))
            .execute()
        )
        rows = customers_result.data or []

        active_rows = [r for r in rows if r.get("status") != "churned"]
        churned_rows = [r for r in rows if r.get("status") == "churned"]
        total_mrr = sum(r.get("mrr") or 0 for r in active_rows)

        # 今月チャーン
        churned_this_month = sum(
            1 for r in churned_rows
            if r.get("churned_at") and str(r["churned_at"]) >= current_month_start.isoformat()
        )

        # revenue_records から拡張/縮小/新規/チャーンMRRを取得（今月分）
        rr_result = (
            db.table("revenue_records")
            .select("record_type, amount")
            .eq("company_id", str(user.company_id))
            .gte("effective_date", current_month_start.date().isoformat())
            .execute()
        )
        rr_rows = rr_result.data or []

        expansion_mrr = sum(r["amount"] for r in rr_rows if r.get("record_type") == "expansion")
        contraction_mrr = sum(r["amount"] for r in rr_rows if r.get("record_type") == "contraction")
        new_mrr = sum(r["amount"] for r in rr_rows if r.get("record_type") == "mrr")
        churn_mrr = sum(r["amount"] for r in rr_rows if r.get("record_type") == "churn")

        # NRR = (MRR + expansion - contraction - churn) / prev_MRR * 100
        # prev_MRR は今月レコードがない場合は現MRRで近似
        prev_mrr = total_mrr - expansion_mrr + contraction_mrr + churn_mrr
        if prev_mrr > 0:
            nrr = (total_mrr + expansion_mrr - contraction_mrr - churn_mrr) / prev_mrr * 100
        else:
            nrr = 100.0

        # チャーン率 = churned_this_month / (active + churned_this_month)
        total_at_month_start = len(active_rows) + churned_this_month
        churn_rate = (churned_this_month / total_at_month_start * 100) if total_at_month_start > 0 else 0.0

        return RevenueSummaryResponse(
            mrr=total_mrr,
            arr=total_mrr * 12,
            nrr=round(nrr, 2),
            churn_rate=round(churn_rate, 2),
            active_customer_count=len(active_rows),
            churned_this_month=churned_this_month,
            expansion_mrr=expansion_mrr,
            contraction_mrr=contraction_mrr,
            new_mrr=new_mrr,
            updated_at=now,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"revenue summary failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/revenue/monthly", response_model=RevenueMonthlyResponse)
async def get_revenue_monthly(
    months: int = Query(12, ge=1, le=36),
    user: JWTClaims = Depends(get_current_user),
):
    """月次売上推移を取得する（過去N ヶ月）。"""
    try:
        db = get_service_client()

        now = datetime.now(timezone.utc)
        # 過去 months ヶ月分の start date を計算（標準ライブラリのみ使用）
        start_year = now.year
        start_month = now.month - (months - 1)
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        from datetime import date as _date
        start_date = _date(start_year, start_month, 1)

        rr_result = (
            db.table("revenue_records")
            .select("record_type, amount, effective_date")
            .eq("company_id", str(user.company_id))
            .gte("effective_date", start_date.isoformat())
            .order("effective_date")
            .execute()
        )
        rr_rows = rr_result.data or []

        # 月別に集計
        monthly_map: dict[str, dict] = {}
        for r in rr_rows:
            eff = str(r.get("effective_date", ""))[:7]  # YYYY-MM
            if not eff:
                continue
            if eff not in monthly_map:
                monthly_map[eff] = {
                    "mrr": 0, "new_mrr": 0, "expansion_mrr": 0,
                    "contraction_mrr": 0, "churn_mrr": 0,
                }
            rec_type = r.get("record_type", "")
            amount = r.get("amount") or 0
            if rec_type == "mrr":
                monthly_map[eff]["mrr"] += amount
                monthly_map[eff]["new_mrr"] += amount
            elif rec_type == "expansion":
                monthly_map[eff]["mrr"] += amount
                monthly_map[eff]["expansion_mrr"] += amount
            elif rec_type == "contraction":
                monthly_map[eff]["mrr"] -= amount
                monthly_map[eff]["contraction_mrr"] += amount
            elif rec_type == "churn":
                monthly_map[eff]["mrr"] -= amount
                monthly_map[eff]["churn_mrr"] += amount

        items = [
            RevenueMonthlyItem(
                month=month,
                mrr=v["mrr"],
                new_mrr=v["new_mrr"],
                expansion_mrr=v["expansion_mrr"],
                contraction_mrr=v["contraction_mrr"],
                churn_mrr=v["churn_mrr"],
                net_change=v["mrr"],
            )
            for month, v in sorted(monthly_map.items())
        ]
        return RevenueMonthlyResponse(items=items, months=months)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"revenue monthly failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/revenue/cohort", response_model=RevenueCohortResponse)
async def get_revenue_cohort(
    user: JWTClaims = Depends(get_current_user),
):
    """コホート分析（月次リテンション率）を取得する。

    customers.onboarded_at で月次コホートを作成し、MRR リテンション率を計算する。
    """
    try:
        db = get_service_client()

        # 全顧客を取得（onboarded_at でコホート分類）
        cust_result = (
            db.table("customers")
            .select("id, mrr, onboarded_at, status, churned_at")
            .eq("company_id", str(user.company_id))
            .not_.is_("onboarded_at", "null")
            .order("onboarded_at")
            .execute()
        )
        cust_rows = cust_result.data or []

        # revenue_records を全取得（コホート計算用）
        rr_result = (
            db.table("revenue_records")
            .select("customer_id, record_type, amount, effective_date")
            .eq("company_id", str(user.company_id))
            .execute()
        )
        rr_rows = rr_result.data or []

        # customer_id → revenue_records のマップ
        rr_by_customer: dict[str, list[dict]] = {}
        for r in rr_rows:
            cid = r["customer_id"]
            rr_by_customer.setdefault(cid, []).append(r)

        # コホート（契約開始月）ごとにグループ化
        cohort_map: dict[str, list[dict]] = {}
        for cust in cust_rows:
            cohort_month = str(cust.get("onboarded_at", ""))[:7]
            if cohort_month:
                cohort_map.setdefault(cohort_month, []).append(cust)

        now = datetime.now(timezone.utc)
        cohorts: list[CohortItem] = []
        for cohort_month, members in sorted(cohort_map.items()):
            initial_mrr = sum(m.get("mrr") or 0 for m in members)
            if initial_mrr == 0:
                continue

            # コホート開始月から現在月まで、月オフセット別リテンション計算
            try:
                cohort_start = datetime.strptime(cohort_month, "%Y-%m").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            months_elapsed = (now.year - cohort_start.year) * 12 + (now.month - cohort_start.month)
            month_data = []
            for offset in range(min(months_elapsed + 1, 24)):  # 最大24ヶ月
                # offset ヶ月時点での MRR を計算
                # チャーンした顧客はその月以降 MRR=0
                period_mrr = 0
                for m in members:
                    cid = m["id"]
                    # チャーン日を確認
                    churned_at = m.get("churned_at")
                    if churned_at:
                        try:
                            churn_dt = datetime.fromisoformat(str(churned_at).replace("Z", "+00:00"))
                            churn_offset = (churn_dt.year - cohort_start.year) * 12 + (churn_dt.month - cohort_start.month)
                            if offset >= churn_offset:
                                continue  # チャーン後はMRR=0
                        except Exception:
                            pass

                    # revenue_records から当該月の累積MRRを計算
                    base_mrr = m.get("mrr") or 0
                    if cid in rr_by_customer:
                        for rec in rr_by_customer[cid]:
                            rec_date = str(rec.get("effective_date", ""))[:7]
                            if not rec_date:
                                continue
                            try:
                                rec_dt = datetime.strptime(rec_date, "%Y-%m").replace(tzinfo=timezone.utc)
                                rec_offset = (rec_dt.year - cohort_start.year) * 12 + (rec_dt.month - cohort_start.month)
                            except ValueError:
                                continue
                            if rec_offset <= offset:
                                if rec.get("record_type") == "expansion":
                                    base_mrr += rec.get("amount") or 0
                                elif rec.get("record_type") in ("contraction", "churn"):
                                    base_mrr -= rec.get("amount") or 0
                    period_mrr += max(base_mrr, 0)

                retention_rate = round(period_mrr / initial_mrr * 100, 2) if initial_mrr > 0 else 0.0
                month_data.append({
                    "month_offset": offset,
                    "retention_rate": retention_rate,
                    "mrr": period_mrr,
                })

            cohorts.append(CohortItem(
                cohort_month=cohort_month,
                initial_mrr=initial_mrr,
                months=month_data,
            ))

        return RevenueCohortResponse(cohorts=cohorts)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"revenue cohort failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Feature Request
# ---------------------------------------------------------------------------


@router.get("/crm/requests", response_model=FeatureRequestListResponse)
async def list_feature_requests(
    customer_id: Optional[UUID] = None,
    feature_status: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """要望一覧を取得する。"""
    try:
        db = get_service_client()
        q = (
            db.table("feature_requests")
            .select(
                "id, customer_id, title, description, category, priority, "
                "ai_category, similar_request_ids, vote_count, status, response, "
                "responded_at, created_at",
                count="exact",
            )
            .eq("company_id", str(user.company_id))
            .order("vote_count", desc=True)
            .range(offset, offset + limit - 1)
        )
        if customer_id:
            q = q.eq("customer_id", str(customer_id))
        if feature_status:
            q = q.eq("status", feature_status)
        if category:
            q = q.eq("category", category)

        result = q.execute()
        items = [FeatureRequestResponse(**r) for r in (result.data or [])]
        total = result.count or 0
        return FeatureRequestListResponse(
            items=items,
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list feature requests failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/crm/requests", response_model=FeatureRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_feature_request(
    body: FeatureRequestCreate,
    user: JWTClaims = Depends(get_current_user),
):
    """要望を登録する。

    - feature_requests テーブルに INSERT
    - 同一顧客の類似タイトル要望がある場合は vote_count をインクリメントして similar_request_ids に追加
    """
    try:
        db = get_service_client()

        # 顧客の存在確認（テナント分離）
        cust_check = (
            db.table("customers")
            .select("id")
            .eq("id", str(body.customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not cust_check.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        # 類似要望検索（タイトルのキーワード一致）
        similar_result = (
            db.table("feature_requests")
            .select("id, title")
            .eq("company_id", str(user.company_id))
            .ilike("title", f"%{body.title[:30]}%")
            .neq("status", "declined")
            .limit(5)
            .execute()
        )
        similar_ids = [r["id"] for r in (similar_result.data or [])]

        insert_data: dict = {
            "company_id": str(user.company_id),
            "customer_id": str(body.customer_id),
            "title": body.title,
            "description": body.description,
            "priority": body.priority or "medium",
            "vote_count": 1,
            "status": "new",
        }
        if body.category:
            insert_data["category"] = body.category
        if similar_ids:
            insert_data["similar_request_ids"] = similar_ids

        result = db.table("feature_requests").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Insert failed")

        return FeatureRequestResponse(**result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create feature request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/crm/requests/{request_id}", response_model=FeatureRequestResponse)
async def update_feature_request(
    request_id: UUID,
    body: FeatureRequestUpdate,
    user: JWTClaims = Depends(require_role("admin")),
):
    """要望ステータスを更新する（admin のみ、楽観的ロック）。

    version=0 は無条件更新。それ以外は created_at の epoch 秒で比較する。
    """
    try:
        db = get_service_client()

        current = (
            db.table("feature_requests")
            .select("id, created_at")
            .eq("id", str(request_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not current.data:
            raise HTTPException(status_code=404, detail="Feature request not found")

        # 楽観的ロック（feature_requests に version カラムがないため created_at epoch で代替）
        if body.version > 0:
            try:
                dt = datetime.fromisoformat(
                    str(current.data.get("created_at", "")).replace("Z", "+00:00")
                )
                current_version = int(dt.timestamp())
            except Exception:
                current_version = 0
            if current_version != body.version:
                raise HTTPException(
                    status_code=409,
                    detail="VERSION_CONFLICT: Feature request has been modified. Please refresh and try again.",
                )

        update_data: dict = {}
        if body.status is not None:
            update_data["status"] = body.status
            if body.status in ("done", "declined") and body.response:
                update_data["responded_at"] = datetime.now(timezone.utc).isoformat()
        if body.priority is not None:
            update_data["priority"] = body.priority
        if body.response is not None:
            update_data["response"] = body.response
            update_data["responded_at"] = datetime.now(timezone.utc).isoformat()

        if not update_data:
            # 何も更新するフィールドがない場合は現在の値をそのまま返す
            result_data = current.data
        else:
            result = (
                db.table("feature_requests")
                .update(update_data)
                .eq("id", str(request_id))
                .eq("company_id", str(user.company_id))
                .execute()
            )
            if not result.data:
                raise HTTPException(status_code=500, detail="Update failed")
            result_data = result.data[0]

        return FeatureRequestResponse(**result_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update feature request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/crm/requests/ranking", response_model=FeatureRequestRankingResponse)
async def get_feature_request_ranking(
    limit: int = Query(20, ge=1, le=100),
    user: JWTClaims = Depends(get_current_user),
):
    """要望ランキングを取得する（投票数 × MRR インパクト順）。

    feature_requests + customers テーブルをJOINして優先スコアを計算する。
    """
    try:
        db = get_service_client()

        # 全アクティブな要望を取得
        fr_result = (
            db.table("feature_requests")
            .select("id, customer_id, title, category, priority, vote_count, status")
            .eq("company_id", str(user.company_id))
            .not_.eq("status", "declined")
            .execute()
        )
        fr_rows = fr_result.data or []
        if not fr_rows:
            return FeatureRequestRankingResponse(items=[], total=0)

        # 顧客MRRを一括取得
        cust_result = (
            db.table("customers")
            .select("id, mrr")
            .eq("company_id", str(user.company_id))
            .execute()
        )
        mrr_map = {r["id"]: (r.get("mrr") or 0) for r in (cust_result.data or [])}

        # スコア計算: vote_count * sqrt(MRR) で優先度を計算
        import math

        ranked = []
        for fr in fr_rows:
            cid = fr.get("customer_id", "")
            mrr = mrr_map.get(cid, 0)
            score = fr.get("vote_count", 1) * math.sqrt(max(mrr, 1))
            ranked.append((score, mrr, fr))

        ranked.sort(key=lambda x: x[0], reverse=True)
        ranked = ranked[:limit]

        items = [
            FeatureRequestRankingItem(
                id=fr["id"],
                title=fr["title"],
                category=fr.get("category"),
                priority=fr["priority"],
                vote_count=fr.get("vote_count") or 1,
                impacted_mrr=mrr,
                status=fr["status"],
            )
            for _, mrr, fr in ranked
        ]
        return FeatureRequestRankingResponse(items=items, total=len(items))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"feature request ranking failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

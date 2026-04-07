"""SFA（Sales Force Automation）エンドポイント — リード・商談・提案書・見積書・契約書管理"""
import logging
from collections import defaultdict
from datetime import datetime, date, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from db import crud_sales

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models — Lead
# ---------------------------------------------------------------------------


class LeadCreate(BaseModel):
    company_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    source: str = "website"                   # website / referral / event / outbound
    source_detail: Optional[str] = None


class LeadUpdate(BaseModel):
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    status: Optional[str] = None             # new / contacted / qualified / unqualified / nurturing
    assigned_to: Optional[UUID] = None
    version: int                             # 楽観的ロック用


class LeadResponse(BaseModel):
    id: UUID
    company_name: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    source: str
    source_detail: Optional[str] = None
    score: int
    score_reasons: list
    status: str
    assigned_to: Optional[UUID] = None
    first_contact_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    representative: Optional[str] = None
    tsr_representative: Optional[str] = None
    representative_phone: Optional[str] = None
    annual_revenue: Optional[int] = None


class LeadListResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    has_more: bool = False


class QualifyResponse(BaseModel):
    lead_id: UUID
    score: int
    score_reasons: list[dict]
    routing: str                             # auto_proposal / manual_review / nurturing
    message: str


# ---------------------------------------------------------------------------
# Request / Response models — Opportunity
# ---------------------------------------------------------------------------


class OpportunityCreate(BaseModel):
    lead_id: Optional[UUID] = None
    title: str
    target_company_name: str
    target_industry: Optional[str] = None
    selected_modules: list[str]              # ["brain", "bpo_core", ...]
    monthly_amount: int
    stage: str = "proposal"
    probability: int = 50
    expected_close_date: Optional[date] = None


class OpportunityUpdate(BaseModel):
    title: Optional[str] = None
    stage: Optional[str] = None              # proposal/quotation/negotiation/contract/won/lost
    probability: Optional[int] = None
    expected_close_date: Optional[date] = None
    lost_reason: Optional[str] = None
    selected_modules: Optional[list[str]] = None
    monthly_amount: Optional[int] = None
    version: int                             # 楽観的ロック用


class OpportunityResponse(BaseModel):
    id: UUID
    lead_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    title: str
    target_company_name: str
    target_industry: Optional[str] = None
    selected_modules: list
    monthly_amount: int
    annual_amount: Optional[int] = None
    stage: str
    probability: int
    expected_close_date: Optional[date] = None
    lost_reason: Optional[str] = None
    stage_changed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class OpportunityListResponse(BaseModel):
    items: list[OpportunityResponse]
    total: int
    has_more: bool = False


class ForecastResponse(BaseModel):
    total_pipeline_amount: int               # パイプライン総額（円）
    weighted_forecast: int                   # 確度加重後予測額
    by_stage: dict                           # ステージ別金額
    by_month: list[dict]                     # 月別予測
    updated_at: datetime


# ---------------------------------------------------------------------------
# Request / Response models — Proposal
# ---------------------------------------------------------------------------


class ProposalGenerateResponse(BaseModel):
    proposal_id: UUID
    opportunity_id: UUID
    status: str
    message: str


class ProposalSendRequest(BaseModel):
    to_email: str
    subject: Optional[str] = None
    body: Optional[str] = None


class ProposalSendResponse(BaseModel):
    proposal_id: UUID
    sent_to: str
    sent_at: datetime
    message: str


class ProposalTrackingResponse(BaseModel):
    proposal_id: UUID
    status: str
    sent_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    open_count: int
    click_count: int
    last_viewed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Request / Response models — Quotation
# ---------------------------------------------------------------------------


class QuotationGenerateResponse(BaseModel):
    quotation_id: UUID
    opportunity_id: UUID
    quotation_number: str
    total: int
    valid_until: date
    status: str
    message: str


class QuotationSendRequest(BaseModel):
    to_email: str


class QuotationSendResponse(BaseModel):
    quotation_id: UUID
    sent_to: str
    sent_at: datetime
    message: str


class QuotationApproveRequest(BaseModel):
    approved: bool
    note: Optional[str] = None


class QuotationApproveResponse(BaseModel):
    quotation_id: UUID
    status: str
    message: str


# ---------------------------------------------------------------------------
# Request / Response models — Contract
# ---------------------------------------------------------------------------


class ContractGenerateResponse(BaseModel):
    contract_id: UUID
    opportunity_id: UUID
    contract_number: str
    status: str
    message: str


class ContractSignResponse(BaseModel):
    contract_id: UUID
    signing_request_id: str
    signing_service: str
    status: str
    message: str


class CloudSignWebhookPayload(BaseModel):
    document_id: str
    status: str                              # signed / rejected / expired
    signed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints — Lead
# ---------------------------------------------------------------------------


@router.post("/sales/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    body: LeadCreate,
    user: JWTClaims = Depends(get_current_user),
):
    """リードを登録する（Webhook・フォーム送信・手動入力から呼ばれる）。"""
    try:
        row = await crud_sales.create_lead(
            company_id=user.company_id,
            data={
                "company_name": body.company_name,
                "contact_name": body.contact_name,
                "contact_email": body.contact_email,
                "contact_phone": body.contact_phone,
                "industry": body.industry,
                "employee_count": body.employee_count,
                "source": body.source,
                "source_detail": body.source_detail,
            },
        )
        return LeadResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create lead failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sales/leads/facets")
async def get_lead_facets(
    industry: Optional[str] = None,
    sub_industry: Optional[str] = None,
    user: JWTClaims = Depends(get_current_user),
):
    """リードのカスケードフィルタ選択肢を返す（大分類→中分類→小分類）。

    RPC (SELECT DISTINCT) で高速に取得する。
    """
    try:
        db = get_service_client()
        result: dict = {}
        cid = user.company_id

        # 大分類: industry の distinct 値
        rpc_res = db.rpc("distinct_lead_values", {
            "p_company_id": cid,
            "p_column_name": "industry",
            "p_filter_column": None,
            "p_filter_value": None,
        }).execute()
        result["industries"] = [r["val"] for r in (rpc_res.data or []) if r.get("val")]

        # 中分類: sub_industry（大分類で絞り込み）
        if industry:
            rpc_res2 = db.rpc("distinct_lead_values", {
                "p_company_id": cid,
                "p_column_name": "sub_industry",
                "p_filter_column": "industry",
                "p_filter_value": industry,
            }).execute()
            result["sub_industries"] = [r["val"] for r in (rpc_res2.data or []) if r.get("val")]

        # 小分類: tsr_category_small（中分類で絞り込み）
        if sub_industry:
            rpc_res3 = db.rpc("distinct_lead_values", {
                "p_company_id": cid,
                "p_column_name": "tsr_category_small",
                "p_filter_column": "sub_industry",
                "p_filter_value": sub_industry,
            }).execute()
            result["categories_small"] = [r["val"] for r in (rpc_res3.data or []) if r.get("val")]

        return result
    except Exception as e:
        logger.error(f"lead facets failed: {e}")
        raise HTTPException(status_code=500, detail=f"lead facets error: {e}")


@router.get("/sales/leads", response_model=LeadListResponse)
async def list_leads(
    lead_status: Optional[str] = Query(None, alias="status"),
    industry: Optional[str] = None,
    sub_industry: Optional[str] = None,
    tsr_category_small: Optional[str] = None,
    min_score: Optional[int] = None,
    min_employees: Optional[int] = Query(None, description="従業員数 下限"),
    max_employees: Optional[int] = Query(None, description="従業員数 上限"),
    min_revenue: Optional[int] = Query(None, description="売上 下限（円）"),
    max_revenue: Optional[int] = Query(None, description="売上 上限（円）"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """リード一覧を取得する（カスケードフィルタ + 範囲フィルタ対応）。"""
    try:
        items, total = await crud_sales.list_leads(
            user.company_id,
            status=lead_status,
            industry=industry,
            sub_industry=sub_industry,
            tsr_category_small=tsr_category_small,
            min_score=min_score,
            min_employees=min_employees,
            max_employees=max_employees,
            min_revenue=min_revenue,
            max_revenue=max_revenue,
            limit=limit,
            offset=offset,
        )
        return LeadListResponse(
            items=[LeadResponse(**i) for i in items],
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list leads failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sales/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """リード詳細を取得する。"""
    try:
        row = await crud_sales.get_lead(user.company_id, str(lead_id))
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")
        return LeadResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get lead failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/sales/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: UUID,
    body: LeadUpdate,
    user: JWTClaims = Depends(get_current_user),
):
    """リード情報を更新する（ステータス変更・担当者アサイン等、楽観的ロック）。"""
    try:
        update_data = body.model_dump(exclude_none=True, exclude={"version"})
        if "assigned_to" in update_data and update_data["assigned_to"] is not None:
            update_data["assigned_to"] = str(update_data["assigned_to"])

        row = await crud_sales.update_lead(
            company_id=user.company_id,
            lead_id=str(lead_id),
            data=update_data,
            expected_version=body.version,
        )
        if not row:
            raise HTTPException(
                status_code=409,
                detail="VERSION_CONFLICT: リードが他のユーザーにより更新されています",
            )
        return LeadResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update lead failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sales/leads/{lead_id}/qualify", response_model=QualifyResponse)
async def qualify_lead(
    lead_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """リードクオリフィケーションを実行してスコアを算出する。"""
    try:
        # リード情報を取得
        lead = await crud_sales.get_lead(user.company_id, str(lead_id))
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")

        from workers.bpo.sales.sfa.lead_qualification_pipeline import (
            run_lead_qualification_pipeline,
        )

        result = await run_lead_qualification_pipeline(
            company_id=user.company_id,
            input_data={
                "company_name": lead.get("company_name"),
                "contact_name": lead.get("contact_name"),
                "contact_email": lead.get("contact_email"),
                "contact_phone": lead.get("contact_phone"),
                "industry": lead.get("industry"),
                "employee_count": lead.get("employee_count"),
                "source": lead.get("source"),
                "need": lead.get("source_detail"),
            },
        )

        # リードのスコアを更新
        routing_map = {
            "QUALIFIED": "auto_proposal",
            "REVIEW": "manual_review",
            "NURTURING": "nurturing",
        }
        routing_label = routing_map.get(result.routing, "nurturing")

        await crud_sales.update_lead(
            company_id=user.company_id,
            lead_id=str(lead_id),
            data={
                "score": result.lead_score,
                "score_reasons": result.score_reasons,
                "status": (
                    "qualified" if result.routing == "QUALIFIED"
                    else "new" if result.routing == "REVIEW"
                    else "nurturing"
                ),
            },
        )

        return QualifyResponse(
            lead_id=lead_id,
            score=result.lead_score,
            score_reasons=result.score_reasons,
            routing=routing_label,
            message=f"スコア {result.lead_score}点 → {routing_label}",
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="lead_qualification_pipeline が利用できません",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"qualify lead failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Opportunity
# ---------------------------------------------------------------------------


@router.post("/sales/opportunities", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED)
async def create_opportunity(
    body: OpportunityCreate,
    user: JWTClaims = Depends(get_current_user),
):
    """商談を作成する。"""
    try:
        data = body.model_dump()
        if data.get("lead_id"):
            data["lead_id"] = str(data["lead_id"])
        if data.get("expected_close_date"):
            data["expected_close_date"] = data["expected_close_date"].isoformat()

        row = await crud_sales.create_opportunity(
            company_id=user.company_id,
            data=data,
        )
        return OpportunityResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create opportunity failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sales/opportunities", response_model=OpportunityListResponse)
async def list_opportunities(
    stage: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """商談一覧を取得する（パイプラインボード用）。"""
    try:
        items, total = await crud_sales.list_opportunities(
            user.company_id,
            stage=stage,
            limit=limit,
            offset=offset,
        )
        return OpportunityListResponse(
            items=[OpportunityResponse(**i) for i in items],
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list opportunities failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/sales/opportunities/{opp_id}", response_model=OpportunityResponse)
async def update_opportunity(
    opp_id: UUID,
    body: OpportunityUpdate,
    user: JWTClaims = Depends(get_current_user),
):
    """商談を更新する（ステージ移動・確度更新等、楽観的ロック）。"""
    try:
        update_data = body.model_dump(exclude_none=True, exclude={"version"})
        if "expected_close_date" in update_data and update_data["expected_close_date"]:
            update_data["expected_close_date"] = update_data["expected_close_date"].isoformat()

        row = await crud_sales.update_opportunity(
            company_id=user.company_id,
            opp_id=str(opp_id),
            data=update_data,
            expected_version=body.version,
        )
        if not row:
            raise HTTPException(
                status_code=409,
                detail="VERSION_CONFLICT: 商談が他のユーザーにより更新されています",
            )

        # stage が won になった場合、customers テーブルへ自動昇格
        if body.stage == "won":
            try:
                await crud_sales.create_customer(
                    company_id=user.company_id,
                    data={
                        "customer_name": row.get("target_company_name", ""),
                        "industry": row.get("target_industry"),
                        "opportunity_id": str(opp_id),
                        "lead_id": row.get("lead_id"),
                        "selected_modules": row.get("selected_modules", []),
                        "mrr": row.get("monthly_amount", 0),
                    },
                )
            except Exception as cust_err:
                logger.warning(f"顧客自動作成に失敗（非致命的）: {cust_err}")

        return OpportunityResponse(**row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update opportunity failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sales/opportunities/forecast", response_model=ForecastResponse)
async def get_sales_forecast(
    user: JWTClaims = Depends(get_current_user),
):
    """売上予測を取得する（パイプライン x 確度の加重計算）。"""
    try:
        # won / lost 以外の全商談を取得
        all_items, _ = await crud_sales.list_opportunities(
            user.company_id,
            limit=500,
        )
        active = [
            i for i in all_items
            if i.get("stage") not in ("won", "lost")
        ]

        total_pipeline = sum(i.get("monthly_amount", 0) * 12 for i in active)
        weighted = sum(
            i.get("monthly_amount", 0) * 12 * i.get("probability", 0) / 100
            for i in active
        )

        # ステージ別金額
        by_stage: dict[str, int] = defaultdict(int)
        for i in active:
            by_stage[i.get("stage", "unknown")] += i.get("monthly_amount", 0) * 12

        # 月別予測（expected_close_date ベース）
        by_month_map: dict[str, dict] = {}
        for i in active:
            ecd = i.get("expected_close_date")
            if ecd:
                month_key = str(ecd)[:7]  # YYYY-MM
            else:
                month_key = "unscheduled"
            if month_key not in by_month_map:
                by_month_map[month_key] = {"month": month_key, "amount": 0, "weighted": 0, "count": 0}
            by_month_map[month_key]["amount"] += i.get("monthly_amount", 0) * 12
            by_month_map[month_key]["weighted"] += int(
                i.get("monthly_amount", 0) * 12 * i.get("probability", 0) / 100
            )
            by_month_map[month_key]["count"] += 1

        by_month = sorted(by_month_map.values(), key=lambda x: x["month"])

        return ForecastResponse(
            total_pipeline_amount=total_pipeline,
            weighted_forecast=int(weighted),
            by_stage=dict(by_stage),
            by_month=by_month,
            updated_at=datetime.now(timezone.utc),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"sales forecast failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Proposal
# ---------------------------------------------------------------------------


@router.get("/sales/proposals")
async def list_proposals(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    user: JWTClaims = Depends(get_current_user),
):
    """提案書一覧を取得する。"""
    try:
        sb = get_service_client()
        query = sb.table("proposals").select("*", count="exact").eq("company_id", user.company_id)
        if status_filter:
            query = query.eq("status", status_filter)
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        result = query.execute()
        return {"items": result.data or [], "total": result.count or 0}
    except Exception:
        return {"items": [], "total": 0}


@router.post("/sales/proposals/{opp_id}/generate", response_model=ProposalGenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_proposal(
    opp_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """提案書を AI で自動生成する。"""
    try:
        # 商談を取得
        opp = await crud_sales.get_opportunity(user.company_id, str(opp_id))
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        from workers.bpo.sales.sfa.proposal_generation_pipeline import (
            run_proposal_generation_pipeline,
        )

        result = await run_proposal_generation_pipeline(
            company_id=user.company_id,
            input_data={
                "lead_id": opp.get("lead_id"),
                "opportunity_id": str(opp_id),
            },
        )

        proposal_id = result.proposal_id or result.final_output.get("proposal_id", "")

        return ProposalGenerateResponse(
            proposal_id=proposal_id,
            opportunity_id=opp_id,
            status="draft",
            message="提案書の生成が完了しました",
        )
    except ImportError:
        # パイプライン未実装時は proposals テーブルに draft を直接作成
        proposal = await crud_sales.create_proposal(
            company_id=user.company_id,
            data={
                "opportunity_id": str(opp_id),
                "status": "draft",
            },
        )
        return ProposalGenerateResponse(
            proposal_id=proposal["id"],
            opportunity_id=opp_id,
            status="draft",
            message="提案書のスケルトンを作成しました（パイプライン未実装）",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"generate proposal failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sales/proposals/{proposal_id}/send", response_model=ProposalSendResponse)
async def send_proposal(
    proposal_id: UUID,
    body: ProposalSendRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """提案書を PDF 添付メールで送付する。"""
    try:
        proposal = await crud_sales.get_proposal(user.company_id, str(proposal_id))
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")

        now = datetime.now(timezone.utc)

        # proposals.status を sent に更新
        await crud_sales.update_proposal(
            company_id=user.company_id,
            proposal_id=str(proposal_id),
            data={
                "status": "sent",
                "sent_at": now.isoformat(),
                "sent_to": body.to_email,
            },
        )

        # メール送付（EmailConnector が利用可能な場合）
        try:
            from workers.connector.email import EmailConnector
            connector = EmailConnector()
            await connector.send(
                to=body.to_email,
                subject=body.subject or "【シャチョツー】ご提案書のご送付",
                body=body.body or "ご提案書を添付いたします。ご査収のほどよろしくお願いいたします。",
            )
        except ImportError:
            logger.info("EmailConnector が利用できないため、メール送信をスキップ")

        return ProposalSendResponse(
            proposal_id=proposal_id,
            sent_to=body.to_email,
            sent_at=now,
            message="提案書を送付しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"send proposal failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/sales/proposals/{proposal_id}/tracking", response_model=ProposalTrackingResponse)
async def get_proposal_tracking(
    proposal_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """提案書の開封・閲覧状況を取得する。"""
    try:
        proposal = await crud_sales.get_proposal(user.company_id, str(proposal_id))
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")

        return ProposalTrackingResponse(
            proposal_id=proposal_id,
            status=proposal.get("status", "draft"),
            sent_at=proposal.get("sent_at"),
            opened_at=proposal.get("opened_at"),
            open_count=proposal.get("open_count", 0),
            click_count=proposal.get("click_count", 0),
            last_viewed_at=proposal.get("last_viewed_at"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"proposal tracking failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Quotation
# ---------------------------------------------------------------------------


@router.get("/sales/quotations")
async def list_quotations(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """見積書一覧を取得する。"""
    try:
        sb = get_service_client()
        result = sb.table("quotations").select("*", count="exact").eq(
            "company_id", user.company_id
        ).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return {"items": result.data or [], "total": result.count or 0}
    except Exception:
        return {"items": [], "total": 0}


@router.post("/sales/quotations/{opp_id}/generate", response_model=QuotationGenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_quotation(
    opp_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """見積書を自動生成する。"""
    try:
        opp = await crud_sales.get_opportunity(user.company_id, str(opp_id))
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        from workers.bpo.sales.sfa.quotation_contract_pipeline import (
            run_quotation_contract_pipeline,
        )

        result = await run_quotation_contract_pipeline(
            company_id=user.company_id,
            input_data={
                "opportunity_id": str(opp_id),
                "selected_modules": opp.get("selected_modules", []),
                "monthly_amount": opp.get("monthly_amount", 0),
                "target_company_name": opp.get("target_company_name", ""),
                "contact_email": "",  # opportunities テーブルにはメール無し
            },
            approval_status="pending",
        )

        quotation_id = result.final_output.get("quotation_id", "")
        quotation_number = result.final_output.get("quotation_number", "")
        total = result.final_output.get("total_amount", opp.get("monthly_amount", 0) * 12)
        valid_until = result.final_output.get("valid_until", (date.today() + __import__("datetime").timedelta(days=30)))

        return QuotationGenerateResponse(
            quotation_id=quotation_id,
            opportunity_id=opp_id,
            quotation_number=quotation_number,
            total=total,
            valid_until=valid_until,
            status="draft",
            message="見積書の生成が完了しました",
        )
    except ImportError:
        # パイプライン未実装時は直接 quotations テーブルに INSERT
        from datetime import timedelta
        qt_number = f"QT-{date.today().strftime('%Y%m')}-{str(__import__('uuid').uuid4())[:4].upper()}"
        monthly = opp.get("monthly_amount", 0) if 'opp' in dir() else 0
        quotation = await crud_sales.create_quotation(
            company_id=user.company_id,
            data={
                "opportunity_id": str(opp_id),
                "quotation_number": qt_number,
                "total": monthly * 12,
                "valid_until": (date.today() + timedelta(days=30)).isoformat(),
                "status": "draft",
            },
        )
        return QuotationGenerateResponse(
            quotation_id=quotation["id"],
            opportunity_id=opp_id,
            quotation_number=qt_number,
            total=monthly * 12,
            valid_until=date.today() + timedelta(days=30),
            status="draft",
            message="見積書スケルトンを作成しました（パイプライン未実装）",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"generate quotation failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sales/quotations/{quotation_id}/send", response_model=QuotationSendResponse)
async def send_quotation(
    quotation_id: UUID,
    body: QuotationSendRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """見積書をメール送付する。"""
    try:
        quotation = await crud_sales.get_quotation(user.company_id, str(quotation_id))
        if not quotation:
            raise HTTPException(status_code=404, detail="Quotation not found")

        now = datetime.now(timezone.utc)

        await crud_sales.update_quotation(
            company_id=user.company_id,
            quotation_id=str(quotation_id),
            data={
                "status": "sent",
                "sent_at": now.isoformat(),
                "sent_to": body.to_email,
            },
        )

        try:
            from workers.connector.email import EmailConnector
            connector = EmailConnector()
            await connector.send(
                to=body.to_email,
                subject="【シャチョツー】お見積書のご送付",
                body="お見積書を添付いたします。ご査収のほどよろしくお願いいたします。",
            )
        except ImportError:
            logger.info("EmailConnector が利用できないため、メール送信をスキップ")

        return QuotationSendResponse(
            quotation_id=quotation_id,
            sent_to=body.to_email,
            sent_at=now,
            message="見積書を送付しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"send quotation failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/sales/quotations/{quotation_id}/approve", response_model=QuotationApproveResponse)
async def approve_quotation(
    quotation_id: UUID,
    body: QuotationApproveRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """見積書を承認または拒否する。"""
    try:
        quotation = await crud_sales.get_quotation(user.company_id, str(quotation_id))
        if not quotation:
            raise HTTPException(status_code=404, detail="Quotation not found")

        new_status = "accepted" if body.approved else "rejected"

        await crud_sales.update_quotation(
            company_id=user.company_id,
            quotation_id=str(quotation_id),
            data={
                "status": new_status,
                "approval_note": body.note,
                "approved_at": datetime.now(timezone.utc).isoformat() if body.approved else None,
            },
        )

        message = "見積書を承認しました" if body.approved else "見積書を拒否しました"

        return QuotationApproveResponse(
            quotation_id=quotation_id,
            status=new_status,
            message=message,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"approve quotation failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Contract
# ---------------------------------------------------------------------------


@router.get("/sales/contracts")
async def list_contracts(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """契約書一覧を取得する。"""
    try:
        sb = get_service_client()
        result = sb.table("contracts").select("*", count="exact").eq(
            "company_id", user.company_id
        ).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return {"items": result.data or [], "total": result.count or 0}
    except Exception:
        return {"items": [], "total": 0}


@router.post("/sales/contracts/{opp_id}/generate", response_model=ContractGenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_contract(
    opp_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """契約書を生成する。"""
    try:
        opp = await crud_sales.get_opportunity(user.company_id, str(opp_id))
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        from workers.bpo.sales.sfa.quotation_contract_pipeline import (
            run_quotation_contract_pipeline,
        )

        result = await run_quotation_contract_pipeline(
            company_id=user.company_id,
            input_data={
                "opportunity_id": str(opp_id),
                "selected_modules": opp.get("selected_modules", []),
                "monthly_amount": opp.get("monthly_amount", 0),
                "target_company_name": opp.get("target_company_name", ""),
                "contact_email": "",
            },
            approval_status="approved",  # 契約書生成は承認済み前提
        )

        contract_id = result.final_output.get("contract_id", "")
        contract_number = result.final_output.get("contract_number", "")

        return ContractGenerateResponse(
            contract_id=contract_id,
            opportunity_id=opp_id,
            contract_number=contract_number,
            status="draft",
            message="契約書の生成が完了しました",
        )
    except ImportError:
        # パイプライン未実装時は直接 contracts テーブルに INSERT
        ct_number = f"CT-{date.today().strftime('%Y%m')}-{str(__import__('uuid').uuid4())[:4].upper()}"
        contract = await crud_sales.create_contract(
            company_id=user.company_id,
            data={
                "opportunity_id": str(opp_id),
                "contract_number": ct_number,
                "status": "draft",
            },
        )
        return ContractGenerateResponse(
            contract_id=contract["id"],
            opportunity_id=opp_id,
            contract_number=ct_number,
            status="draft",
            message="契約書スケルトンを作成しました（パイプライン未実装）",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"generate contract failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sales/contracts/{contract_id}/sign", response_model=ContractSignResponse)
async def send_for_signing(
    contract_id: UUID,
    user: JWTClaims = Depends(require_role("admin")),
):
    """CloudSign に電子署名依頼を送信する（admin のみ）。
    CloudSign 未設定時は consent_flow（アプリ内同意）にフォールバック。
    """
    try:
        contract = await crud_sales.get_contract(user.company_id, str(contract_id))
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        # consent_flow パイプラインで電子同意を実行
        from workers.bpo.sales.sfa.consent_flow import run_consent_flow_pipeline

        opp_id = contract.get("opportunity_id")
        opp = None
        if opp_id:
            opp = await crud_sales.get_opportunity(user.company_id, opp_id)

        to_email = contract.get("contact_email") or (opp or {}).get("contact_email") or user.email

        result = await run_consent_flow_pipeline(
            company_id=user.company_id,
            contract_id=str(contract_id),
            contract_data={
                "contract_title": f"サービス利用契約書 {contract.get('contract_number', '')}",
                "contract_number": contract.get("contract_number", ""),
                "company_name": (opp or {}).get("target_company_name", ""),
                "modules": (opp or {}).get("selected_modules", []),
                "monthly_amount": (opp or {}).get("monthly_amount", 0),
            },
            to_email=to_email,
        )

        if not result.success:
            raise HTTPException(
                status_code=500,
                detail=f"署名依頼に失敗しました: {result.failed_step}",
            )

        # contracts テーブルを更新
        await crud_sales.update_contract(
            company_id=user.company_id,
            contract_id=str(contract_id),
            data={
                "status": "sent",
                "consent_token": result.consent_token,
            },
        )

        return ContractSignResponse(
            contract_id=contract_id,
            signing_request_id=result.consent_token or "",
            signing_service="consent_flow",
            status="sent",
            message="電子同意の依頼を送信しました",
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="consent_flow パイプラインが利用できません",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"send for signing failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sales/contracts/{contract_id}/webhook", status_code=status.HTTP_200_OK)
async def cloudsign_webhook(
    contract_id: UUID,
    payload: CloudSignWebhookPayload,
    request: Request,
):
    """CloudSign 署名完了 Webhook を受信する（認証不要）。

    注: メインの CloudSign Webhook は /webhooks/cloudsign で受信する。
    このエンドポイントは後方互換性のために残す。
    """
    try:
        db = get_service_client()

        if payload.status == "signed":
            # contracts テーブルを更新
            db.table("contracts").update({
                "status": "active",
                "cloudsign_status": "signed",
                "signed_at": payload.signed_at or datetime.now(timezone.utc).isoformat(),
            }).eq("id", str(contract_id)).execute()

            logger.info(f"Contract signed via legacy webhook: contract_id={contract_id}")

        elif payload.status == "rejected":
            db.table("contracts").update({
                "status": "rejected",
                "cloudsign_status": "rejected",
            }).eq("id", str(contract_id)).execute()

        elif payload.status == "expired":
            db.table("contracts").update({
                "status": "expired",
                "cloudsign_status": "expired",
            }).eq("id", str(contract_id)).execute()

        return {"received": True, "message": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cloudsign webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

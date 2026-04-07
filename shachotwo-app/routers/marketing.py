"""マーケティング自動化エンドポイント — アウトリーチ・企業リサーチ・シグナル管理"""
import asyncio
import logging
import uuid as uuid_mod
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from db import crud_sales
from workers.micro.models import MicroAgentInput

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class OutreachRunRequest(BaseModel):
    industries: Optional[list[str]] = None   # 対象業種（Noneなら全業種）
    target_count: int = 400                  # 1日の目標アウトリーチ数
    dry_run: bool = False                    # Trueなら実行せずシミュレーションのみ


class OutreachRunResponse(BaseModel):
    job_id: str
    status: str                              # queued / running / completed
    target_count: int
    message: str


class OutreachStatusResponse(BaseModel):
    date: str
    researched_count: int
    outreached_count: int
    lp_viewed_count: int
    lead_converted_count: int
    meeting_booked_count: int
    industries: list[dict]                   # 業種別内訳


class OutreachPerformanceItem(BaseModel):
    period: date
    industry: str
    researched_count: int
    outreached_count: int
    lp_viewed_count: int
    lead_converted_count: int
    meeting_booked_count: int
    open_rate: Optional[float] = None
    click_rate: Optional[float] = None


class OutreachPerformanceResponse(BaseModel):
    items: list[OutreachPerformanceItem]
    total: int


class ResearchedCompany(BaseModel):
    id: UUID
    company_name: str
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    pain_points: Optional[list[str]] = None
    signal_temperature: Optional[str] = None  # hot / warm / cold
    last_outreached_at: Optional[datetime] = None
    created_at: datetime


class ResearchedCompanyListResponse(BaseModel):
    items: list[ResearchedCompany]
    total: int
    has_more: bool = False


class EnrichRequest(BaseModel):
    company_name: str
    corporate_number: Optional[str] = None   # 法人番号（あればより精度UP）


class EnrichResponse(BaseModel):
    company_name: str
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    representative: Optional[str] = None
    address: Optional[str] = None
    corporate_number: Optional[str] = None
    pain_points: Optional[list[str]] = None
    message: str


class SignalItem(BaseModel):
    id: UUID
    lead_id: Optional[UUID] = None
    company_name: str
    temperature: str                         # hot / warm / cold
    signal_type: str                         # page_view / cta_click / form_submit / email_open
    signal_count: int
    last_signal_at: datetime
    created_at: datetime


class SignalListResponse(BaseModel):
    items: list[SignalItem]
    total: int
    hot_count: int
    warm_count: int
    cold_count: int


class ABTestResult(BaseModel):
    variant_name: str
    industry: str
    outreached_count: int
    open_rate: float
    click_rate: float
    lead_converted_count: int
    period_start: date
    period_end: date


class ABTestListResponse(BaseModel):
    items: list[ABTestResult]
    total: int


# ---------------------------------------------------------------------------
# Dashboard response models
# ---------------------------------------------------------------------------


class OutreachSummary(BaseModel):
    sent_today: int
    replied_today: int
    hot_leads_today: int
    reply_rate: float
    hot_lead_rate: float


class IndustryPerformance(BaseModel):
    industry: str
    industry_label: str
    sent: int
    replied: int
    hot_leads: int
    reply_rate: float


class HotLead(BaseModel):
    id: str
    company_name: str
    contact_name: Optional[str] = None
    industry: str
    industry_label: str
    score: int
    last_activity_at: str
    pain_points: list[str] = []


class OutreachDashboardResponse(BaseModel):
    summary: OutreachSummary
    industry_performance: list[IndustryPerformance]
    hot_leads: list[HotLead]


# ---------------------------------------------------------------------------
# Industry label mapping
# ---------------------------------------------------------------------------

INDUSTRY_LABELS: dict[str, str] = {
    "construction": "建設業",
    "manufacturing": "製造業",
    "medical_welfare": "医療・福祉",
    "real_estate": "不動産業",
    "logistics": "運輸・物流",
    "wholesale": "卸売業",
    "dental": "歯科",
    "restaurant": "飲食",
    "professional": "士業",
    "unknown": "その他",
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/marketing/outreach/dashboard", response_model=OutreachDashboardResponse)
async def get_outreach_dashboard(
    user: JWTClaims = Depends(get_current_user),
):
    """アウトリーチダッシュボード — 本日のサマリー・業種別・ホットリードを一括返却。"""
    try:
        d_str = date.today().isoformat()
        db = get_service_client()

        # --- 1. outreach_performance から本日分を取得 ---
        perf_items, _ = await crud_sales.list_performance(
            user.company_id,
            start_date=d_str,
            end_date=d_str,
        )

        # --- 2. 業種別集計 ---
        industry_map: dict[str, dict] = {}
        total_sent = 0
        total_replied = 0

        for item in perf_items:
            ind = item.get("industry", "unknown")
            sent = item.get("outreached_count", 0)
            replied = item.get("lead_converted_count", 0) + item.get("meeting_booked_count", 0)

            if ind not in industry_map:
                industry_map[ind] = {"sent": 0, "replied": 0, "hot_leads": 0}
            industry_map[ind]["sent"] += sent
            industry_map[ind]["replied"] += replied
            total_sent += sent
            total_replied += replied

        # --- 3. ホットリード（leads テーブル score >= 80, 本日更新分） ---
        hot_leads_result = (
            db.table("leads")
            .select("id, company_name, contact_name, industry, score, updated_at, score_reasons")
            .eq("company_id", user.company_id)
            .gte("score", 80)
            .gte("updated_at", f"{d_str}T00:00:00Z")
            .order("score", desc=True)
            .limit(20)
            .execute()
        )
        hot_leads_data = hot_leads_result.data or []

        # ホットリードの業種別カウントを加算
        for lead in hot_leads_data:
            ind = lead.get("industry", "unknown")
            if ind not in industry_map:
                industry_map[ind] = {"sent": 0, "replied": 0, "hot_leads": 0}
            industry_map[ind]["hot_leads"] += 1

        hot_leads_today = len(hot_leads_data)

        # --- 4. レスポンス組み立て ---
        summary = OutreachSummary(
            sent_today=total_sent,
            replied_today=total_replied,
            hot_leads_today=hot_leads_today,
            reply_rate=round(total_replied / total_sent, 4) if total_sent > 0 else 0.0,
            hot_lead_rate=round(hot_leads_today / total_sent, 4) if total_sent > 0 else 0.0,
        )

        industry_performance = [
            IndustryPerformance(
                industry=ind,
                industry_label=INDUSTRY_LABELS.get(ind, ind),
                sent=data["sent"],
                replied=data["replied"],
                hot_leads=data["hot_leads"],
                reply_rate=round(data["replied"] / data["sent"], 4) if data["sent"] > 0 else 0.0,
            )
            for ind, data in sorted(industry_map.items(), key=lambda x: x[1]["sent"], reverse=True)
        ]

        hot_leads = [
            HotLead(
                id=str(lead["id"]),
                company_name=lead.get("company_name", ""),
                contact_name=lead.get("contact_name"),
                industry=lead.get("industry", "unknown"),
                industry_label=INDUSTRY_LABELS.get(lead.get("industry", "unknown"), lead.get("industry", "unknown")),
                score=lead.get("score", 0),
                last_activity_at=lead.get("updated_at", ""),
                pain_points=lead.get("score_reasons") or [],
            )
            for lead in hot_leads_data
        ]

        return OutreachDashboardResponse(
            summary=summary,
            industry_performance=industry_performance,
            hot_leads=hot_leads,
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"outreach dashboard failed: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"outreach dashboard error: {e}")


@router.post("/marketing/outreach/run", response_model=OutreachRunResponse)
async def run_outreach(
    body: OutreachRunRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """アウトリーチパイプラインを手動実行する。

    - 企業リサーチ → ペイン推定 → メール/フォーム送信（最大400件/日）
    - dry_run=True の場合は実行せずシミュレーション結果のみ返す
    """
    try:
        from workers.bpo.sales.marketing.outreach_pipeline import run_outreach_pipeline

        job_id = str(uuid_mod.uuid4())
        input_data: dict = {
            "source": "manual",
            "target_count": body.target_count,
            "dry_run": body.dry_run,
        }
        if body.industries:
            input_data["industries"] = body.industries

        # 非同期でパイプラインをキック（バックグラウンド実行）
        import asyncio
        asyncio.create_task(
            run_outreach_pipeline(
                company_id=user.company_id,
                input_data=input_data,
            )
        )

        return OutreachRunResponse(
            job_id=job_id,
            status="queued",
            target_count=body.target_count,
            message="アウトリーチパイプラインをキューに投入しました",
        )
    except ImportError:
        logger.warning("outreach_pipeline が利用できません。ジョブをキューに投入しました（ダミー）")
        return OutreachRunResponse(
            job_id=str(uuid_mod.uuid4()),
            status="queued",
            target_count=body.target_count,
            message="アウトリーチパイプラインをキューに投入しました（パイプライン未実装）",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"outreach run failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/outreach/status", response_model=OutreachStatusResponse)
async def get_outreach_status(
    target_date: Optional[date] = Query(None, description="対象日（Noneなら本日）"),
    user: JWTClaims = Depends(get_current_user),
):
    """本日（または指定日）のアウトリーチ状況を取得する。"""
    try:
        d = target_date or date.today()
        d_str = d.isoformat()

        # outreach_performance から当日集計
        items, _ = await crud_sales.list_performance(
            user.company_id,
            start_date=d_str,
            end_date=d_str,
        )

        # lead_activities から当日の各種カウント
        activities_q = (
            get_service_client()
            .table("lead_activities")
            .select("activity_type", count="exact")
            .eq("company_id", user.company_id)
            .gte("created_at", f"{d_str}T00:00:00Z")
            .lte("created_at", f"{d_str}T23:59:59Z")
        )
        activities_result = activities_q.execute()
        activities = activities_result.data or []

        # 集計
        researched = sum(i.get("researched_count", 0) for i in items)
        outreached = sum(i.get("outreached_count", 0) for i in items)
        lp_viewed = sum(
            1 for a in activities if a.get("activity_type") == "lp_view"
        )
        lead_converted = sum(
            1 for a in activities if a.get("activity_type") == "lead_converted"
        )
        meeting_booked = sum(
            1 for a in activities if a.get("activity_type") == "meeting_booked"
        )

        # 業種別内訳
        industries: list[dict] = []
        industry_map: dict[str, dict] = {}
        for i in items:
            ind = i.get("industry", "unknown")
            if ind not in industry_map:
                industry_map[ind] = {
                    "industry": ind,
                    "researched_count": 0,
                    "outreached_count": 0,
                }
            industry_map[ind]["researched_count"] += i.get("researched_count", 0)
            industry_map[ind]["outreached_count"] += i.get("outreached_count", 0)
        industries = list(industry_map.values())

        return OutreachStatusResponse(
            date=d_str,
            researched_count=researched,
            outreached_count=outreached,
            lp_viewed_count=lp_viewed,
            lead_converted_count=lead_converted,
            meeting_booked_count=meeting_booked,
            industries=industries,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"outreach status failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/outreach/performance", response_model=OutreachPerformanceResponse)
async def get_outreach_performance(
    industry: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    user: JWTClaims = Depends(get_current_user),
):
    """業種別アウトリーチパフォーマンスを取得する（過去N日）。"""
    try:
        start_date = (date.today() - timedelta(days=days)).isoformat()

        items, total = await crud_sales.list_performance(
            user.company_id,
            industry=industry,
            start_date=start_date,
            limit=days * 20,  # 業種数 × 日数分を十分取得
        )

        performance_items = []
        for i in items:
            performance_items.append(OutreachPerformanceItem(
                period=i.get("period", date.today()),
                industry=i.get("industry", "unknown"),
                researched_count=i.get("researched_count", 0),
                outreached_count=i.get("outreached_count", 0),
                lp_viewed_count=i.get("lp_viewed_count", 0),
                lead_converted_count=i.get("lead_converted_count", 0),
                meeting_booked_count=i.get("meeting_booked_count", 0),
                open_rate=i.get("open_rate"),
                click_rate=i.get("click_rate"),
            ))

        return OutreachPerformanceResponse(
            items=performance_items,
            total=total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"outreach performance failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/research/companies", response_model=ResearchedCompanyListResponse)
async def list_researched_companies(
    industry: Optional[str] = None,
    temperature: Optional[str] = Query(None, description="hot / warm / cold"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """リサーチ済み企業一覧を取得する（leads テーブルの source='outreach'）。"""
    try:
        items, total = await crud_sales.list_leads(
            user.company_id,
            source="outreach",
            industry=industry,
            temperature=temperature,
            limit=limit,
            offset=offset,
        )

        researched = []
        for item in items:
            researched.append(ResearchedCompany(
                id=item["id"],
                company_name=item.get("company_name", ""),
                industry=item.get("industry"),
                employee_count=item.get("employee_count"),
                pain_points=item.get("pain_points"),
                signal_temperature=item.get("signal_temperature"),
                last_outreached_at=item.get("last_outreached_at"),
                created_at=item.get("created_at", datetime.now(timezone.utc)),
            ))

        return ResearchedCompanyListResponse(
            items=researched,
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list researched companies failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/marketing/research/enrich", response_model=EnrichResponse)
async def enrich_company(
    body: EnrichRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """企業情報を手動エンリッチする（gBizINFO + LLMペイン推定）。"""
    try:
        from workers.micro.company_researcher import run_company_researcher

        result = await run_company_researcher(MicroAgentInput(
            company_id=user.company_id,
            agent_name="company_researcher",
            payload={
                "name": body.company_name,
                "industry": "",
                "employee_count": None,
                "business_overview": "",
                "corporate_number": body.corporate_number,
            },
            context={},
        ))

        if not result.success:
            raise HTTPException(
                status_code=500,
                detail=f"エンリッチに失敗しました: {result.result.get('error', 'unknown')}",
            )

        pain_details = [
            p.get("detail", "") for p in result.result.get("pain_points", [])
        ]

        return EnrichResponse(
            company_name=body.company_name,
            industry=result.result.get("industry"),
            employee_count=result.result.get("employee_count"),
            representative=None,
            address=None,
            corporate_number=body.corporate_number,
            pain_points=pain_details or None,
            message="企業情報のエンリッチが完了しました",
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="company_researcher マイクロエージェントが利用できません",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"enrich company failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/signals", response_model=SignalListResponse)
async def list_signals(
    temperature: Optional[str] = Query(None, description="hot / warm / cold"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """シグナル一覧を取得する（LP閲覧・CTA クリック・資料DL 等の行動シグナル）。"""
    try:
        db = get_service_client()

        # lead_activities から集計してシグナル温度付きで返す
        q = (
            db.table("lead_activities")
            .select("*, leads!inner(company_name, signal_temperature)")
            .eq("company_id", user.company_id)
            .in_("activity_type", ["page_view", "cta_click", "form_submit", "email_open", "doc_download"])
            .order("created_at", desc=True)
        )
        if temperature:
            q = q.eq("leads.signal_temperature", temperature)

        q = q.range(offset, offset + limit - 1)
        result = q.execute()
        activities = result.data or []

        # シグナルアイテムを構築
        signal_items: list[SignalItem] = []
        hot_count = warm_count = cold_count = 0

        for a in activities:
            lead_data = a.get("leads", {}) or {}
            temp = lead_data.get("signal_temperature", "cold")
            if temp == "hot":
                hot_count += 1
            elif temp == "warm":
                warm_count += 1
            else:
                cold_count += 1

            signal_items.append(SignalItem(
                id=a.get("id", str(uuid_mod.uuid4())),
                lead_id=a.get("lead_id"),
                company_name=lead_data.get("company_name", ""),
                temperature=temp,
                signal_type=a.get("activity_type", "unknown"),
                signal_count=a.get("signal_count", 1),
                last_signal_at=a.get("created_at", datetime.now(timezone.utc)),
                created_at=a.get("created_at", datetime.now(timezone.utc)),
            ))

        total = len(signal_items)
        # フォールバック: JOIN 失敗時は単純クエリ
        if not signal_items and not temperature:
            simple_q = (
                db.table("lead_activities")
                .select("*", count="exact")
                .eq("company_id", user.company_id)
                .in_("activity_type", ["page_view", "cta_click", "form_submit", "email_open", "doc_download"])
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            simple_result = simple_q.execute()
            total = simple_result.count or 0
            for a in (simple_result.data or []):
                signal_items.append(SignalItem(
                    id=a.get("id", str(uuid_mod.uuid4())),
                    lead_id=a.get("lead_id"),
                    company_name="",
                    temperature="cold",
                    signal_type=a.get("activity_type", "unknown"),
                    signal_count=1,
                    last_signal_at=a.get("created_at", datetime.now(timezone.utc)),
                    created_at=a.get("created_at", datetime.now(timezone.utc)),
                ))
                cold_count += 1

        return SignalListResponse(
            items=signal_items,
            total=total,
            hot_count=hot_count,
            warm_count=warm_count,
            cold_count=cold_count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list signals failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# 製造業ターゲットリスト — Request / Response models
# ---------------------------------------------------------------------------

GBIZINFO_MANUFACTURING_INDUSTRY_CODE = "E"  # 製造業の産業分類コード

# 製造業ペインヒント（サブ業種別）
_PAIN_HINTS_BY_SUB_INDUSTRY: dict[str, list[str]] = {
    "金属加工":    ["見積書の手作業入力が多い", "図面管理が属人化している", "外注先への発注業務が煩雑"],
    "機械製造":    ["部品調達リードタイムの管理が困難", "設備保全スケジュールが紙管理", "品質トレーサビリティに課題"],
    "電子部品":    ["在庫管理が複雑", "顧客図面との照合に工数がかかる", "納期管理のExcel運用に限界"],
    "食品製造":    ["原材料ロット管理が煩雑", "賞味期限管理の自動化が必要", "衛生管理記録の紙運用"],
    "化学製品":    ["配合レシピの版数管理が煩雑", "規制対応文書の作成に工数", "安全データシート管理"],
    "プラスチック": ["金型管理台帳が紙", "成形条件のデータ化ができていない", "不良品分析の工数"],
    "自動車部品":  ["QMS文書管理が煩雑", "EDI対応の工数が大きい", "トレーサビリティ要件への対応"],
    "その他製造":  ["バックオフィス業務のデジタル化が遅れている", "受注〜出荷の情報連携に課題"],
}


class ManufacturingListRequest(BaseModel):
    """製造業ターゲットリスト取得リクエスト"""
    prefectures: list[str] = []        # 対象都道府県（空なら全国）
    min_employees: int = 10
    max_employees: int = 1000
    sub_industries: list[str] = []     # 対象サブ業種（空なら全部）
    revenue_segments: list[str] = []   # target_core / target_upper
    limit: int = 100


class ManufacturingCompanyItem(BaseModel):
    corporate_number: str
    company_name: str
    sub_industry: str
    prefecture: str
    employee_count: Optional[int]
    capital_stock: Optional[int]
    revenue_segment: str
    priority_tier: str
    pain_hints: list[str]


class ManufacturingListResponse(BaseModel):
    items: list[ManufacturingCompanyItem]
    total: int
    segments_summary: dict  # {"S": 12, "A": 45, "B": 120, "C": 23}


# ---------------------------------------------------------------------------
# gBizINFO API ヘルパー
# ---------------------------------------------------------------------------

async def _fetch_gbizinfo_manufacturing(
    prefectures: list[str],
    min_employees: int,
    max_employees: int,
    limit: int,
) -> list[dict]:
    """gBizINFO REST API から製造業企業を取得する。

    レート制限: 1秒あたり最大10リクエスト。バッチ処理でスリープを挟む。
    取得できない場合は空リストを返す（フォールバック: DBの既存リードを使用）。
    """
    import os
    import httpx

    api_key = os.environ.get("GBIZINFO_API_KEY", "")
    base_url = "https://info.gbiz.go.jp/hojin/v1/hojin"

    results: list[dict] = []
    page_size = min(limit, 10)  # gBizINFO は最大10件/リクエスト
    pages_needed = (limit + page_size - 1) // page_size
    prefecture_targets = prefectures if prefectures else [""]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for prefecture in prefecture_targets:
            if len(results) >= limit:
                break
            for page in range(1, pages_needed + 1):
                if len(results) >= limit:
                    break
                params: dict = {
                    "type": GBIZINFO_MANUFACTURING_INDUSTRY_CODE,
                    "page": page,
                    "limit": page_size,
                }
                if prefecture:
                    params["prefecture"] = prefecture
                if min_employees:
                    params["employee_number_from"] = min_employees
                if max_employees:
                    params["employee_number_to"] = max_employees

                headers = {}
                if api_key:
                    headers["X-hojinInfo-api-token"] = api_key

                try:
                    resp = await client.get(base_url, params=params, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        hits: list[dict] = data.get("hojin-infos", [])
                        results.extend(hits)
                    elif resp.status_code == 429:
                        # レート制限: 待機して再試行
                        await asyncio.sleep(2)
                    else:
                        logger.warning(f"gBizINFO API returned {resp.status_code}")
                        break
                except Exception as exc:
                    logger.warning(f"gBizINFO request failed: {exc}")
                    break

                # レート制限対策: 1秒あたり最大10リクエスト
                await asyncio.sleep(0.12)

    return results[:limit]


def _map_gbizinfo_to_company(raw: dict) -> dict:
    """gBizINFO レスポンスを内部スキーマにマッピングする。"""
    employee_str: str = raw.get("employee_number", "") or ""
    employee_count: Optional[int] = None
    if employee_str.isdigit():
        employee_count = int(employee_str)

    capital_str: str = raw.get("capital_stock", "") or ""
    capital_stock: Optional[int] = None
    if capital_str.replace(",", "").isdigit():
        capital_stock = int(capital_str.replace(",", ""))

    return {
        "corporate_number": raw.get("corporate_number", ""),
        "company_name": raw.get("name", ""),
        "industry_text": raw.get("business_summary", "") or raw.get("business_items", ""),
        "prefecture": raw.get("prefecture_name", ""),
        "city": raw.get("city_name", ""),
        "employee_count": employee_count,
        "capital_stock": capital_stock,
        "annual_revenue": None,   # gBizINFO 無料枠では売上非公開
        "operating_profit": None,
        "website_url": raw.get("company_url", ""),
        "representative": raw.get("representative_name", ""),
        "business_overview": raw.get("business_summary", ""),
    }


# ---------------------------------------------------------------------------
# 製造業ターゲットリスト エンドポイント
# ---------------------------------------------------------------------------

@router.post("/marketing/manufacturing/list", response_model=ManufacturingListResponse)
async def get_manufacturing_target_list(
    req: ManufacturingListRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """製造業ターゲット企業リストを取得する。

    1. gBizINFO API から製造業企業を取得
    2. セグメント分類（segmentation.classify_company）
    3. 優先度順にソート（S → A → B → C）
    4. 結果を leads テーブルにも保存（重複チェック: corporate_number）
    """
    from workers.bpo.sales.segmentation import (
        classify_company,
        detect_sub_industry,
        _PAIN_HINTS_BY_SUB_INDUSTRY,
    )

    try:
        # gBizINFO から取得
        raw_companies = await _fetch_gbizinfo_manufacturing(
            prefectures=req.prefectures,
            min_employees=req.min_employees,
            max_employees=req.max_employees,
            limit=req.limit * 3,  # フィルタ後に limit 件確保するため多めに取得
        )

        db = get_service_client()
        tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        items: list[ManufacturingCompanyItem] = []
        segments_summary: dict[str, int] = {"S": 0, "A": 0, "B": 0, "C": 0}

        for raw in raw_companies:
            mapped = _map_gbizinfo_to_company(raw)
            seg = classify_company(
                annual_revenue=mapped["annual_revenue"],
                operating_profit=mapped["operating_profit"],
                employee_count=mapped["employee_count"],
                industry_text=mapped["industry_text"],
                capital_stock=mapped["capital_stock"],
            )

            # サブ業種フィルタ
            if req.sub_industries and seg.sub_industry not in req.sub_industries:
                continue

            # 売上セグメントフィルタ
            if req.revenue_segments and seg.revenue_segment not in req.revenue_segments:
                continue

            segments_summary[seg.priority_tier] = segments_summary.get(seg.priority_tier, 0) + 1
            pain_hints = _PAIN_HINTS_BY_SUB_INDUSTRY.get(seg.sub_industry, _PAIN_HINTS_BY_SUB_INDUSTRY["その他製造"])

            items.append(ManufacturingCompanyItem(
                corporate_number=mapped["corporate_number"],
                company_name=mapped["company_name"],
                sub_industry=seg.sub_industry,
                prefecture=mapped["prefecture"],
                employee_count=mapped["employee_count"],
                capital_stock=mapped["capital_stock"],
                revenue_segment=seg.revenue_segment,
                priority_tier=seg.priority_tier,
                pain_hints=pain_hints,
            ))

            # leads テーブルに upsert（corporate_number で重複チェック）
            if mapped["corporate_number"]:
                upsert_data = {
                    "company_id": user.company_id,
                    "company_name": mapped["company_name"],
                    "industry": "manufacturing",
                    "sub_industry": seg.sub_industry,
                    "corporate_number": mapped["corporate_number"],
                    "employee_count": mapped["employee_count"],
                    "capital_stock": mapped["capital_stock"],
                    "annual_revenue": mapped["annual_revenue"],
                    "operating_profit": mapped["operating_profit"],
                    "prefecture": mapped["prefecture"],
                    "city": mapped["city"],
                    "website_url": mapped["website_url"],
                    "representative": mapped["representative"],
                    "business_overview": mapped["business_overview"],
                    "revenue_segment": seg.revenue_segment,
                    "profit_segment": seg.profit_segment,
                    "priority_tier": seg.priority_tier,
                    "source": "gbizinfo",
                    "pain_points": pain_hints,
                }
                try:
                    db.table("leads").upsert(
                        upsert_data,
                        on_conflict="company_id,corporate_number",
                    ).execute()
                except Exception as upsert_err:
                    logger.warning(f"leads upsert skipped: {upsert_err}")

        # 優先度順ソート
        items.sort(key=lambda x: tier_order.get(x.priority_tier, 9))
        items = items[: req.limit]

        return ManufacturingListResponse(
            items=items,
            total=len(items),
            segments_summary=segments_summary,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_manufacturing_target_list failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/manufacturing/segments", response_model=dict)
async def get_segment_summary(
    user: JWTClaims = Depends(get_current_user),
):
    """製造業リードのセグメント別サマリーを返す。

    leads テーブルから industry='manufacturing' を集計し、
    revenue_segment / profit_segment / priority_tier / sub_industry / prefecture 別の件数を返す。
    """
    try:
        db = get_service_client()
        result = (
            db.table("leads")
            .select(
                "revenue_segment, profit_segment, priority_tier, sub_industry, prefecture",
                count="exact",
            )
            .eq("company_id", user.company_id)
            .eq("industry", "manufacturing")
            .execute()
        )
        rows: list[dict] = result.data or []

        summary: dict[str, dict[str, int]] = {
            "priority_tier": {},
            "revenue_segment": {},
            "profit_segment": {},
            "sub_industry": {},
            "prefecture": {},
        }

        for row in rows:
            for key in summary:
                val = row.get(key) or "unknown"
                summary[key][val] = summary[key].get(val, 0) + 1

        return {
            "total": result.count or len(rows),
            **summary,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_segment_summary failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/marketing/manufacturing/enrich-all")
async def enrich_all_manufacturing_leads(
    user: JWTClaims = Depends(require_role("admin")),
):
    """既存の製造業リードを一括で gBizINFO エンリッチする。

    corporate_number が設定済みのリードに対して gBizINFO から詳細情報を取得し、
    セグメント分類を再計算して leads テーブルを更新する。
    バッチ処理でレート制限（1秒10リクエスト）に配慮する。
    """
    from workers.bpo.sales.segmentation import classify_company
    import os
    import httpx

    try:
        db = get_service_client()

        # corporate_number 設定済みの製造業リードを取得
        result = (
            db.table("leads")
            .select("id, corporate_number, company_name, employee_count")
            .eq("company_id", user.company_id)
            .eq("industry", "manufacturing")
            .not_.is_("corporate_number", "null")
            .execute()
        )
        leads = result.data or []

        if not leads:
            return {"message": "エンリッチ対象のリードがありません", "updated": 0}

        api_key = os.environ.get("GBIZINFO_API_KEY", "")
        base_url = "https://info.gbiz.go.jp/hojin/v1/hojin"
        updated = 0

        async with httpx.AsyncClient(timeout=10.0) as client:
            for idx, lead in enumerate(leads):
                corp_num = lead.get("corporate_number")
                if not corp_num:
                    continue

                headers = {}
                if api_key:
                    headers["X-hojinInfo-api-token"] = api_key

                try:
                    resp = await client.get(
                        f"{base_url}/{corp_num}",
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        logger.warning(f"gBizINFO {corp_num}: status {resp.status_code}")
                        continue

                    data = resp.json()
                    raw = (data.get("hojin-infos") or [{}])[0]
                    mapped = _map_gbizinfo_to_company(raw)
                    seg = classify_company(
                        annual_revenue=mapped["annual_revenue"],
                        operating_profit=mapped["operating_profit"],
                        employee_count=mapped["employee_count"] or lead.get("employee_count"),
                        industry_text=mapped["industry_text"],
                        capital_stock=mapped["capital_stock"],
                    )

                    db.table("leads").update({
                        "sub_industry": seg.sub_industry,
                        "revenue_segment": seg.revenue_segment,
                        "profit_segment": seg.profit_segment,
                        "priority_tier": seg.priority_tier,
                        "capital_stock": mapped["capital_stock"],
                        "annual_revenue": mapped["annual_revenue"],
                        "operating_profit": mapped["operating_profit"],
                        "website_url": mapped["website_url"],
                        "representative": mapped["representative"],
                        "business_overview": mapped["business_overview"],
                        "prefecture": mapped["prefecture"],
                        "city": mapped["city"],
                    }).eq("id", lead["id"]).execute()
                    updated += 1

                except Exception as exc:
                    logger.warning(f"enrich lead {corp_num} failed: {exc}")

                # レート制限: 1秒10リクエスト
                if (idx + 1) % 10 == 0:
                    await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.12)

        return {
            "message": f"エンリッチが完了しました（{updated}/{len(leads)}件更新）",
            "total": len(leads),
            "updated": updated,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"enrich_all_manufacturing_leads failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# 業界団体名簿スクレイピング — Request / Response models
# ---------------------------------------------------------------------------


class ScrapeDirectoriesRequest(BaseModel):
    """業界団体名簿スクレイピングリクエスト"""
    sources: list[str] = ["jdmia"]   # スクレイピング対象（現在 "jdmia" のみ対応）
    max_per_source: Optional[int] = None  # 取得上限（None=全件。テスト用）
    dry_run: bool = False             # Trueなら取得のみでleadsへの保存をスキップ


class ScrapeDirectoriesResponse(BaseModel):
    """業界団体名簿スクレイピング結果"""
    total_scraped: int
    total_upserted: int
    total_skipped: int
    sources_summary: dict   # {"jdmia": {"scraped": 418, "upserted": 410, "skipped": 8}}
    message: str


# sub_industry → industry（leadsテーブルの industry カラム用）マッピング
_DIRECTORY_INDUSTRY = "manufacturing"

# sub_industry ラベル → leads.sub_industry マッピング（正規化）
_SUB_INDUSTRY_NORMALIZE: dict[str, str] = {
    "プラスチック金型": "プラスチック",
    "プレス金型": "金属加工",
    "ダイカスト金型": "金属加工",
    "鋳造金型": "金属加工",
    "鍛造金型": "金属加工",
    "ゴム金型": "その他製造",
    "ガラス金型": "その他製造",
    "複合金型": "金属加工",
    "金型": "金属加工",
}


@router.post("/marketing/scrape-directories", response_model=ScrapeDirectoriesResponse)
async def scrape_industry_directories(
    body: ScrapeDirectoriesRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """業界団体名簿をスクレイピングして leads テーブルに保存する。

    処理フロー:
        1. 対象 sources ごとに scrape_all_directories() を呼ぶ
        2. 各エントリを leads テーブルに upsert（company_name + source で重複チェック）
        3. source = "industry_directory"、sub_industry を製造業カテゴリに正規化
        4. 結果サマリーを返す

    制約:
        - admin ロールのみ実行可能
        - 1リクエスト/秒のレート制限（全件スクレイピングは数分かかる）
        - dry_run=True でスクレイピングのみ実行（leads への書き込みをスキップ）
    """
    from workers.micro.industry_directory_scraper import (
        scrape_jdmia_members,
        DirectoryEntry,
    )

    try:
        db = get_service_client()
        total_scraped = 0
        total_upserted = 0
        total_skipped = 0
        sources_summary: dict[str, dict[str, int]] = {}

        for source in body.sources:
            if source == "jdmia":
                scraped_entries: list[DirectoryEntry] = await scrape_jdmia_members(
                    max_companies=body.max_per_source,
                )
            else:
                logger.warning(f"Unknown source: {source} — skipped")
                continue

            scraped = len(scraped_entries)
            upserted = 0
            skipped = 0

            if not body.dry_run:
                for entry in scraped_entries:
                    if not entry.company_name:
                        skipped += 1
                        continue

                    # sub_industry を製造業の共通カテゴリに正規化
                    normalized_sub = _SUB_INDUSTRY_NORMALIZE.get(
                        entry.sub_industry, "金属加工"
                    )

                    upsert_data: dict = {
                        "company_id": user.company_id,
                        "company_name": entry.company_name,
                        "industry": _DIRECTORY_INDUSTRY,
                        "sub_industry": normalized_sub,
                        "address": entry.address or None,
                        "phone": entry.phone or None,
                        "fax": entry.fax or None,
                        "representative": entry.representative or None,
                        "website_url": entry.website_url or None,
                        "email": entry.email or None,
                        "source": "industry_directory",
                        "source_detail": source,
                    }

                    try:
                        db.table("leads").upsert(
                            upsert_data,
                            on_conflict="company_id,company_name,source",
                        ).execute()
                        upserted += 1
                    except Exception as upsert_err:
                        logger.warning(
                            f"leads upsert skipped ({entry.company_name}): {upsert_err}"
                        )
                        skipped += 1

            total_scraped += scraped
            total_upserted += upserted
            total_skipped += skipped
            sources_summary[source] = {
                "scraped": scraped,
                "upserted": upserted,
                "skipped": skipped,
            }

        action = "スクレイピング（dry_run）" if body.dry_run else "取込"
        return ScrapeDirectoriesResponse(
            total_scraped=total_scraped,
            total_upserted=total_upserted,
            total_skipped=total_skipped,
            sources_summary=sources_summary,
            message=(
                f"業界団体名簿の{action}が完了しました。"
                f"取得={total_scraped}件、保存={total_upserted}件、スキップ={total_skipped}件"
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"scrape_industry_directories failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/marketing/ab-tests", response_model=ABTestListResponse)
async def list_ab_tests(
    industry: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    user: JWTClaims = Depends(get_current_user),
):
    """メールA/Bテスト結果一覧を取得する（outreach_performance の email_variant 別集計）。"""
    try:
        # email_variant が設定されたレコードを取得
        db = get_service_client()
        q = (
            db.table("outreach_performance")
            .select("*")
            .eq("company_id", user.company_id)
            .not_.is_("email_variant", "null")
            .order("period", desc=True)
            .limit(limit * 5)  # variant 別に集約するため多めに取得
        )
        if industry:
            q = q.eq("industry", industry)

        result = q.execute()
        rows = result.data or []

        # variant × industry でグルーピング
        groups: dict[tuple[str, str], dict] = {}
        for r in rows:
            variant = r.get("email_variant", "default")
            ind = r.get("industry", "unknown")
            key = (variant, ind)
            if key not in groups:
                groups[key] = {
                    "variant_name": variant,
                    "industry": ind,
                    "outreached_count": 0,
                    "total_opens": 0,
                    "total_clicks": 0,
                    "lead_converted_count": 0,
                    "period_start": r.get("period"),
                    "period_end": r.get("period"),
                }
            g = groups[key]
            g["outreached_count"] += r.get("outreached_count", 0)
            g["total_opens"] += r.get("open_count", 0)
            g["total_clicks"] += r.get("click_count", 0)
            g["lead_converted_count"] += r.get("lead_converted_count", 0)
            # 期間範囲を更新
            rp = r.get("period")
            if rp and (not g["period_start"] or rp < g["period_start"]):
                g["period_start"] = rp
            if rp and (not g["period_end"] or rp > g["period_end"]):
                g["period_end"] = rp

        ab_items: list[ABTestResult] = []
        for g in list(groups.values())[:limit]:
            outreached = g["outreached_count"] or 1  # division by zero 防止
            ab_items.append(ABTestResult(
                variant_name=g["variant_name"],
                industry=g["industry"],
                outreached_count=g["outreached_count"],
                open_rate=round(g["total_opens"] / outreached, 4),
                click_rate=round(g["total_clicks"] / outreached, 4),
                lead_converted_count=g["lead_converted_count"],
                period_start=g["period_start"] or date.today(),
                period_end=g["period_end"] or date.today(),
            ))

        return ABTestListResponse(
            items=ab_items,
            total=len(ab_items),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list ab tests failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# 製造業リード一括ロード
# ---------------------------------------------------------------------------


class ManufacturingLeadLoadRequest(BaseModel):
    target_sub_industries: Optional[list[str]] = None  # Noneなら全サブ業種
    extract_contacts: bool = True                       # HP→メール/フォーム抽出も実行するか
    dry_run: bool = False                               # Trueなら DBへの書き込みをスキップ


class ManufacturingLeadLoadResponse(BaseModel):
    job_id: str
    status: str           # queued
    message: str
    dry_run: bool


class ManufacturingLeadLoadResult(BaseModel):
    job_id: str
    total_fetched: int
    total_inserted: int
    total_updated: int
    by_sub_industry: dict[str, int]
    by_priority: dict[str, int]
    contacts_extracted: int
    emails_found: int
    forms_found: int


class KintoneMfgImportRequest(BaseModel):
    """kintone 製造業リードアプリから leads へ取り込む。"""

    app_id: str
    query: Optional[str] = None
    dry_run: bool = False
    probe_size: int = 10  # 先頭 N 件で検証してから全件続行


class KintoneMfgImportResponse(BaseModel):
    job_id: str
    status: str
    message: str
    dry_run: bool


@router.post(
    "/marketing/manufacturing/load-leads",
    response_model=ManufacturingLeadLoadResponse,
    summary="製造業リードをgBizINFOから一括ロード",
)
async def load_manufacturing_leads_endpoint(
    body: ManufacturingLeadLoadRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> ManufacturingLeadLoadResponse:
    """gBizINFO APIから製造業企業を一括取得し、leadsテーブルに流し込む。

    - サブ業種ごとにキーワード検索 → 重複排除 → セグメント分類（S/A/B/C）
    - extract_contacts=True の場合、企業HPからメール/フォームURLも自動抽出
    - バックグラウンドジョブとして実行し、即座にジョブIDを返す
    - API トークンは環境変数 GBIZ_API_TOKEN から取得

    **必要ロール**: admin
    """
    import os

    api_token = os.environ.get("GBIZ_API_TOKEN", "")
    if not api_token:
        raise HTTPException(
            status_code=400,
            detail="GBIZ_API_TOKEN が設定されていません。環境変数を確認してください。",
        )

    job_id = str(uuid_mod.uuid4())

    async def _run() -> None:
        try:
            from workers.bpo.sales.manufacturing_lead_loader import load_manufacturing_leads
            result = await load_manufacturing_leads(
                api_token=api_token,
                company_id=user.company_id,
                target_sub_industries=body.target_sub_industries,
                extract_contacts=body.extract_contacts,
                dry_run=body.dry_run,
            )
            logger.info(
                f"[load-leads job={job_id}] 完了: "
                f"取得={result['total_fetched']}, "
                f"INSERT={result['total_inserted']}, "
                f"UPDATE={result['total_updated']}, "
                f"メール={result['emails_found']}, "
                f"フォーム={result['forms_found']}"
            )
        except Exception as e:
            logger.error(f"[load-leads job={job_id}] 失敗: {e}", exc_info=True)

    asyncio.create_task(_run())

    dry_label = "（dry_run: DB書き込みなし）" if body.dry_run else ""
    return ManufacturingLeadLoadResponse(
        job_id=job_id,
        status="queued",
        message=f"製造業リード一括ロードをバックグラウンドで開始しました{dry_label}。"
                f"ログで進捗を確認してください。",
        dry_run=body.dry_run,
    )

@router.post(
    "/marketing/manufacturing/import-kintone",
    response_model=KintoneMfgImportResponse,
    summary="kintoneから製造業リードを取り込む（プローブ後に全件）",
)
async def import_kintone_manufacturing_endpoint(
    body: KintoneMfgImportRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneMfgImportResponse:
    """tool_connections の kintone 認証でアプリを読み、先頭 probe_size 件で検証後に全件 upsert する。

    - background_jobs に状態を記録（GET /jobs/{job_id} で参照）
    - kintone_field_mappings があればフィールドコード変換に使用
    """
    from db.supabase import get_service_client
    from workers.bpo.sales.background_job_service import (
        create_job_row,
        fetch_field_mappings_for_app,
        mark_job_completed,
        mark_job_failed,
        mark_job_running,
    )
    from workers.bpo.sales.kintone_credentials import resolve_kintone_credentials
    from workers.bpo.sales.kintone_manufacturing_import import import_manufacturing_leads_from_kintone

    app_id = body.app_id.strip()
    if not app_id:
        raise HTTPException(status_code=400, detail="app_id が空です。")
    probe = body.probe_size
    if probe < 1 or probe > 500:
        raise HTTPException(status_code=422, detail="probe_size は 1〜500 で指定してください。")

    try:
        creds = resolve_kintone_credentials(str(user.company_id))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job_id = str(uuid_mod.uuid4())
    company_id = str(user.company_id)

    db_init = get_service_client()
    create_job_row(
        db_init,
        job_id=job_id,
        company_id=company_id,
        job_type="kintone_mfg_import",
        payload={
            "app_id": app_id,
            "query": body.query,
            "probe_size": probe,
            "dry_run": body.dry_run,
        },
    )

    async def _run() -> None:
        dbj = get_service_client()
        try:
            mark_job_running(dbj, job_id)
            fm = fetch_field_mappings_for_app(dbj, company_id, app_id)
            result = await import_manufacturing_leads_from_kintone(
                subdomain=creds["subdomain"],
                api_token=creds["api_token"],
                app_id=app_id,
                company_id=company_id,
                base_query=body.query or "",
                probe_size=probe,
                dry_run=body.dry_run,
                field_mappings=fm,
            )
            mark_job_completed(dbj, job_id, result)
            logger.info(
                "[kintone-import job=%s] 完了 probe_ok=%s received=%s upsert_ok=%s skipped=%s dry_run=%s",
                job_id,
                result.get("probe_ok"),
                result.get("total_received"),
                result.get("total_upsert_ok"),
                result.get("total_skipped"),
                result.get("dry_run"),
            )
        except Exception as e:
            logger.error("[kintone-import job=%s] 失敗: %s", job_id, e, exc_info=True)
            mark_job_failed(dbj, job_id, str(e))

    asyncio.create_task(_run())

    dry_label = "（dry_run）" if body.dry_run else ""
    return KintoneMfgImportResponse(
        job_id=job_id,
        status="queued",
        message=(
            f"kintone（アプリ {app_id}）から取り込みを開始しました{dry_label}。"
            f" まず {probe} 件で検証し、成功後に全件を続行します。"
            f" 状態は GET /api/v1/jobs/{job_id} で確認できます。"
        ),
        dry_run=body.dry_run,
    )


class KintoneConstructionImportRequest(KintoneMfgImportRequest):
    """建設業リード用（ボディは製造と同型）。"""


@router.post(
    "/marketing/construction/import-kintone",
    response_model=KintoneMfgImportResponse,
    summary="kintoneから建設業リードを取り込む（プローブ後に全件）",
)
async def import_kintone_construction_endpoint(
    body: KintoneConstructionImportRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneMfgImportResponse:
    from db.supabase import get_service_client
    from workers.bpo.sales.background_job_service import (
        create_job_row,
        fetch_field_mappings_for_app,
        mark_job_completed,
        mark_job_failed,
        mark_job_running,
    )
    from workers.bpo.sales.kintone_construction_import import import_construction_leads_from_kintone
    from workers.bpo.sales.kintone_credentials import resolve_kintone_credentials

    app_id = body.app_id.strip()
    if not app_id:
        raise HTTPException(status_code=400, detail="app_id が空です。")
    probe = body.probe_size
    if probe < 1 or probe > 500:
        raise HTTPException(status_code=422, detail="probe_size は 1〜500 で指定してください。")

    try:
        creds = resolve_kintone_credentials(str(user.company_id))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job_id = str(uuid_mod.uuid4())
    company_id = str(user.company_id)

    db_init = get_service_client()
    create_job_row(
        db_init,
        job_id=job_id,
        company_id=company_id,
        job_type="kintone_construction_import",
        payload={
            "app_id": app_id,
            "query": body.query,
            "probe_size": probe,
            "dry_run": body.dry_run,
        },
    )

    async def _run() -> None:
        dbj = get_service_client()
        try:
            mark_job_running(dbj, job_id)
            fm = fetch_field_mappings_for_app(dbj, company_id, app_id)
            result = await import_construction_leads_from_kintone(
                subdomain=creds["subdomain"],
                api_token=creds["api_token"],
                app_id=app_id,
                company_id=company_id,
                base_query=body.query or "",
                probe_size=probe,
                dry_run=body.dry_run,
                field_mappings=fm,
            )
            mark_job_completed(dbj, job_id, result)
            logger.info(
                "[kintone-const-import job=%s] 完了 received=%s upsert_ok=%s",
                job_id,
                result.get("total_received"),
                result.get("total_upsert_ok"),
            )
        except Exception as e:
            logger.error("[kintone-const-import job=%s] 失敗: %s", job_id, e, exc_info=True)
            mark_job_failed(dbj, job_id, str(e))

    asyncio.create_task(_run())

    dry_label = "（dry_run）" if body.dry_run else ""
    return KintoneMfgImportResponse(
        job_id=job_id,
        status="queued",
        message=(
            f"kintone（建設・アプリ {app_id}）から取り込みを開始しました{dry_label}。"
            f" 状態は GET /api/v1/jobs/{job_id} で確認できます。"
        ),
        dry_run=body.dry_run,
    )

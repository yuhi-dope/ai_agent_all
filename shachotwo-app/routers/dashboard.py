"""ダッシュボード集計エンドポイント"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.jwt import JWTClaims
from brain.proactive.expansion_scorer import ExpansionScorer, ExpansionResult, NextStep
from brain.analytics.benchmark_aggregator import BenchmarkAggregator, BenchmarkResult
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RecentKnowledgeItem(BaseModel):
    """最近のナレッジアイテム概要"""
    id: UUID
    title: str
    department: str
    created_at: datetime


class RecentProposal(BaseModel):
    """最近の提案概要"""
    id: UUID
    title: str
    type: str
    priority: str
    status: str
    created_at: datetime


class MonthlyCostResponse(BaseModel):
    """月間AIコストレスポンス"""
    month: str
    total_cost_yen: float
    extraction_cost_yen: float
    qa_cost_yen: float
    extraction_count: int
    qa_count: int


class DashboardSummaryResponse(BaseModel):
    """ダッシュボード集計レスポンス"""
    knowledge_count: int
    proposal_count: int
    recent_knowledge: list[RecentKnowledgeItem]
    recent_proposals: list[RecentProposal]
    snapshot_date: Optional[datetime] = None
    wau_rate: Optional[float] = None          # 週次アクティブ率 (0.0〜1.0)
    wau_active_users: Optional[int] = None    # 過去7日のアクティブユーザー数
    wau_total_users: Optional[int] = None     # テナント総ユーザー数


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    user: JWTClaims = Depends(get_current_user),
):
    """ダッシュボード集計 — ナレッジ数・提案数・最新アイテム・スナップショット日時"""
    db = get_service_client()
    company_id = user.company_id

    try:
        # ナレッジ総数（アクティブのみ）
        knowledge_count_result = db.table("knowledge_items") \
            .select("id", count="exact") \
            .eq("company_id", company_id) \
            .eq("is_active", True) \
            .execute()
        knowledge_count = knowledge_count_result.count or 0

        # 提案数（pending / proposed ステータス）
        proposal_count_result = db.table("proactive_proposals") \
            .select("id", count="exact") \
            .eq("company_id", company_id) \
            .in_("status", ["pending", "proposed"]) \
            .execute()
        proposal_count = proposal_count_result.count or 0

        # 最新ナレッジ5件
        recent_knowledge_result = db.table("knowledge_items") \
            .select("id, title, department, created_at") \
            .eq("company_id", company_id) \
            .eq("is_active", True) \
            .order("created_at", desc=True) \
            .limit(5) \
            .execute()

        # 最新提案3件
        recent_proposals_result = db.table("proactive_proposals") \
            .select("id, title, type, priority, status, created_at") \
            .eq("company_id", company_id) \
            .order("created_at", desc=True) \
            .limit(3) \
            .execute()

        # 最新スナップショット日時
        snapshot_result = db.table("company_state_snapshots") \
            .select("snapshot_at") \
            .eq("company_id", company_id) \
            .order("snapshot_at", desc=True) \
            .limit(1) \
            .execute()
        snapshot_date = (
            snapshot_result.data[0]["snapshot_at"]
            if snapshot_result.data
            else None
        )

        # WAU（週次アクティブ率）
        # 過去7日以内にQ&Aまたはナレッジアップロードをしたユニークユーザー数 / 総ユーザー数
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        total_users_result = db.table("users") \
            .select("id", count="exact") \
            .eq("company_id", company_id) \
            .execute()
        total_users = total_users_result.count or 0

        wau_rate = None
        wau_active = None
        if total_users > 0:
            # Q&Aアクティブユーザー
            qa_active_result = db.table("qa_sessions") \
                .select("user_id") \
                .eq("company_id", company_id) \
                .gte("created_at", week_ago) \
                .execute()
            qa_user_ids = {r["user_id"] for r in qa_active_result.data}

            # ナレッジ投入アクティブユーザー
            knowledge_active_result = db.table("knowledge_sessions") \
                .select("user_id") \
                .eq("company_id", company_id) \
                .gte("created_at", week_ago) \
                .execute()
            knowledge_user_ids = {r["user_id"] for r in knowledge_active_result.data}

            active_user_ids = qa_user_ids | knowledge_user_ids
            wau_active = len(active_user_ids)
            wau_rate = round(wau_active / total_users, 4)

    except Exception as e:
        logger.error(f"Dashboard summary query failed: {e}")
        raise HTTPException(status_code=500, detail="ダッシュボード集計の取得に失敗しました")

    return DashboardSummaryResponse(
        knowledge_count=knowledge_count,
        proposal_count=proposal_count,
        recent_knowledge=[
            RecentKnowledgeItem(**item)
            for item in recent_knowledge_result.data
        ],
        recent_proposals=[
            RecentProposal(**item)
            for item in recent_proposals_result.data
        ],
        snapshot_date=snapshot_date,
        wau_rate=wau_rate,
        wau_active_users=wau_active,
        wau_total_users=total_users if total_users > 0 else None,
    )


@router.get("/dashboard/monthly-cost", response_model=MonthlyCostResponse)
async def get_monthly_cost(
    user: JWTClaims = Depends(get_current_user),
):
    """今月のAI利用コスト集計"""
    from datetime import timezone

    db = get_service_client()
    company_id = user.company_id

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_str = now.strftime("%Y-%m")

    try:
        # Extraction cost from knowledge_sessions
        extraction_result = db.table("knowledge_sessions") \
            .select("cost_yen") \
            .eq("company_id", company_id) \
            .gte("created_at", month_start) \
            .execute()

        extraction_costs = [r["cost_yen"] for r in extraction_result.data if r.get("cost_yen")]
        extraction_cost = sum(extraction_costs)
        extraction_count = len(extraction_costs)

        # QA cost from qa_sessions
        qa_result = db.table("qa_sessions") \
            .select("cost_yen") \
            .eq("company_id", company_id) \
            .gte("created_at", month_start) \
            .execute()

        qa_costs = [r["cost_yen"] for r in qa_result.data if r.get("cost_yen")]
        qa_cost = sum(qa_costs)
        qa_count = len(qa_costs)

        return MonthlyCostResponse(
            month=month_str,
            total_cost_yen=round(extraction_cost + qa_cost, 2),
            extraction_cost_yen=round(extraction_cost, 2),
            qa_cost_yen=round(qa_cost, 2),
            extraction_count=extraction_count,
            qa_count=qa_count,
        )

    except Exception as e:
        logger.error(f"Monthly cost query failed: {e}")
        raise HTTPException(status_code=500, detail="月間コストの取得に失敗しました")


# ---------------------------------------------------------------------------
# Expansion endpoint
# ---------------------------------------------------------------------------

class NextStepResponse(BaseModel):
    """次のステップ1件のレスポンス"""
    feature: str
    reason: str
    expected_benefit: str
    action_url: str
    priority: int


class ExpansionResponse(BaseModel):
    """Land and Expand スコアリング結果"""
    company_id: str
    current_stage: str  # "onboarding" | "active_single" | "active_multi" | "power_user"
    usage_score: float  # 0.0-1.0
    next_steps: list[NextStepResponse]
    computed_at: datetime


@router.get("/dashboard/expansion", response_model=ExpansionResponse)
async def get_expansion(
    user: JWTClaims = Depends(get_current_user),
):
    """利用パターンを分析して次のステップを提案する（Land and Expand）"""
    try:
        scorer = ExpansionScorer()
        result: ExpansionResult = await scorer.score(company_id=user.company_id)
        return ExpansionResponse(
            company_id=result.company_id,
            current_stage=result.current_stage,
            usage_score=result.usage_score,
            next_steps=[
                NextStepResponse(
                    feature=s.feature,
                    reason=s.reason,
                    expected_benefit=s.expected_benefit,
                    action_url=s.action_url,
                    priority=s.priority,
                )
                for s in result.next_steps
            ],
            computed_at=result.computed_at,
        )
    except Exception as e:
        logger.error(f"Expansion scoring failed: {e}")
        raise HTTPException(status_code=500, detail="次のステップの取得に失敗しました")


# ---------------------------------------------------------------------------
# Benchmark endpoint (REQ-2004: ネットワーク効果・匿名ベンチマーク)
# ---------------------------------------------------------------------------

class BenchmarkMetricResponse(BaseModel):
    """1メトリクスのベンチマーク結果"""
    metric_name: str
    my_value: float
    industry_avg: float
    industry_percentile: int   # 0-100
    unit: str
    insight: str


class BenchmarkResponse(BaseModel):
    """匿名ベンチマーク結果"""
    company_id: str
    industry: str
    company_count: Optional[int]  # k-匿名化: 5社未満はNone
    metrics: list[BenchmarkMetricResponse]
    computed_at: datetime
    is_available: bool
    unavailable_reason: Optional[str] = None


@router.get("/dashboard/benchmark", response_model=BenchmarkResponse)
async def get_benchmark(
    user: JWTClaims = Depends(get_current_user),
):
    """同業種の匿名ベンチマークを返す（REQ-2004）。

    k-匿名化: 同業種5社未満の場合は is_available=False で返す。
    他社の個別データは返さず、集計値のみ。
    """
    try:
        aggregator = BenchmarkAggregator()
        result: BenchmarkResult = await aggregator.compute(company_id=user.company_id)
        return BenchmarkResponse(
            company_id=result.company_id,
            industry=result.industry,
            company_count=result.company_count,
            metrics=[
                BenchmarkMetricResponse(
                    metric_name=m.metric_name,
                    my_value=m.my_value,
                    industry_avg=m.industry_avg,
                    industry_percentile=m.industry_percentile,
                    unit=m.unit,
                    insight=m.insight,
                )
                for m in result.metrics
            ],
            computed_at=result.computed_at,
            is_available=result.is_available,
            unavailable_reason=result.unavailable_reason,
        )
    except Exception as e:
        logger.error(f"Benchmark computation failed: {e}")
        raise HTTPException(status_code=500, detail="ベンチマークデータの取得に失敗しました")

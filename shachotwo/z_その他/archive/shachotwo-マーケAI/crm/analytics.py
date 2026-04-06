"""コンバージョン分析"""

from __future__ import annotations

from pydantic import BaseModel

from crm.db import get_client


class PipelineSummary(BaseModel):
    total_outreach: int = 0
    total_lp_views: int = 0
    total_leads: int = 0
    total_deals: int = 0
    total_won: int = 0
    total_mrr: int = 0


class IndustryConversion(BaseModel):
    industry: str
    outreach_count: int = 0
    lp_view_count: int = 0
    lead_count: int = 0
    won_count: int = 0
    conversion_rate: float = 0.0


def get_pipeline_summary() -> PipelineSummary:
    """パイプラインサマリー取得"""
    client = get_client()

    outreach = client.table("apo_outreach_logs").select("id", count="exact").execute()
    views = client.table("apo_page_views").select("id", count="exact").execute()
    leads = client.table("apo_leads").select("id", count="exact").execute()
    deals = client.table("apo_deals").select("id", count="exact").execute()
    won = client.table("apo_deals").select("id, monthly_amount", count="exact").eq("stage", "closed_won").execute()

    total_mrr = sum(d.get("monthly_amount", 0) for d in won.data) if won.data else 0

    return PipelineSummary(
        total_outreach=outreach.count or 0,
        total_lp_views=views.count or 0,
        total_leads=leads.count or 0,
        total_deals=deals.count or 0,
        total_won=won.count or 0,
        total_mrr=total_mrr,
    )


def get_conversion_by_industry() -> list[IndustryConversion]:
    """業種別コンバージョン率"""
    client = get_client()
    companies = client.table("apo_companies").select("id, industry").execute().data or []

    by_industry: dict[str, IndustryConversion] = {}
    for c in companies:
        ind = c.get("industry", "不明")
        if ind not in by_industry:
            by_industry[ind] = IndustryConversion(industry=ind)

    # TODO: 各テーブルをJOINしてカウント（RPCファンクションの方が効率的）
    return list(by_industry.values())

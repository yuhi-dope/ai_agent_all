"""SFA / CRM / CS 用 DB CRUD ヘルパー。

全関数:
- async
- company_id フィルタ必須（テナント分離）
- 型ヒント付き

対象テーブル:
  leads, lead_activities, opportunities, proposals, quotations,
  contracts, customers, customer_health, revenue_records,
  feature_requests, support_tickets, ticket_messages,
  win_loss_patterns, outreach_performance, cs_feedback,
  scoring_model_versions
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _db():
    """サービスクライアントを取得する（RLS バイパス）。"""
    return get_service_client()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """値が None のキーを除外する（PATCH 用）。"""
    return {k: v for k, v in d.items() if v is not None}


# ═══════════════════════════════════════════════════════════════════════════
# leads
# ═══════════════════════════════════════════════════════════════════════════

async def create_lead(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """leads テーブルに新規リードを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "score": 0,
        "score_reasons": [],
        "status": "new",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "version": 1,
        **data,
    }
    result = _db().table("leads").insert(row).execute()
    return result.data[0]


async def get_lead(company_id: str, lead_id: str) -> dict[str, Any] | None:
    """leads テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("leads")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", lead_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def list_leads(
    company_id: str,
    *,
    status: Optional[str] = None,
    industry: Optional[str] = None,
    min_score: Optional[int] = None,
    source: Optional[str] = None,
    temperature: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """leads テーブルからフィルタ付きで一覧取得する。

    Returns:
        (items, total_count)
    """
    q = (
        _db()
        .table("leads")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if status:
        q = q.eq("status", status)
    if industry:
        q = q.eq("industry", industry)
    if min_score is not None:
        q = q.gte("score", min_score)
    if source:
        q = q.eq("source", source)
    if temperature:
        q = q.eq("signal_temperature", temperature)

    q = q.order("score", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_lead(
    company_id: str,
    lead_id: str,
    data: dict[str, Any],
    expected_version: Optional[int] = None,
) -> dict[str, Any] | None:
    """leads テーブルを更新する（楽観的ロック対応）。

    expected_version を指定した場合、version が一致しない場合は None を返す。
    """
    update_data = {
        **_strip_none(data),
        "updated_at": _now_iso(),
    }
    q = (
        _db()
        .table("leads")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", lead_id)
    )
    if expected_version is not None:
        q = q.eq("version", expected_version)
        update_data["version"] = expected_version + 1

    result = q.execute()
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# lead_activities
# ═══════════════════════════════════════════════════════════════════════════

async def create_activity(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """lead_activities テーブルにアクティビティを記録する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("lead_activities").insert(row).execute()
    return result.data[0]


async def list_activities_by_lead(
    company_id: str,
    lead_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """lead_activities テーブルからリード別に取得する。"""
    result = (
        _db()
        .table("lead_activities")
        .select("*")
        .eq("company_id", company_id)
        .eq("lead_id", lead_id)
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data


# ═══════════════════════════════════════════════════════════════════════════
# opportunities
# ═══════════════════════════════════════════════════════════════════════════

async def create_opportunity(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """opportunities テーブルに商談を INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "stage": "proposal",
        "probability": 50,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "version": 1,
        **data,
    }
    # annual_amount を自動算出
    monthly = row.get("monthly_amount", 0)
    if monthly and "annual_amount" not in row:
        row["annual_amount"] = monthly * 12

    result = _db().table("opportunities").insert(row).execute()
    return result.data[0]


async def get_opportunity(company_id: str, opp_id: str) -> dict[str, Any] | None:
    """opportunities テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("opportunities")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", opp_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def list_opportunities(
    company_id: str,
    *,
    stage: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """opportunities テーブルからフィルタ付きで一覧取得する。"""
    q = (
        _db()
        .table("opportunities")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if stage:
        q = q.eq("stage", stage)

    q = q.order("updated_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_opportunity(
    company_id: str,
    opp_id: str,
    data: dict[str, Any],
    expected_version: Optional[int] = None,
) -> dict[str, Any] | None:
    """opportunities テーブルを更新する（楽観的ロック対応）。"""
    update_data = {
        **_strip_none(data),
        "updated_at": _now_iso(),
    }
    if "stage" in update_data:
        update_data["stage_changed_at"] = _now_iso()
    # annual_amount 再計算
    if "monthly_amount" in update_data:
        update_data["annual_amount"] = update_data["monthly_amount"] * 12

    q = (
        _db()
        .table("opportunities")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", opp_id)
    )
    if expected_version is not None:
        q = q.eq("version", expected_version)
        update_data["version"] = expected_version + 1

    result = q.execute()
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# proposals
# ═══════════════════════════════════════════════════════════════════════════

async def create_proposal(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """proposals テーブルに INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "draft",
        "open_count": 0,
        "click_count": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("proposals").insert(row).execute()
    return result.data[0]


async def get_proposal(company_id: str, proposal_id: str) -> dict[str, Any] | None:
    """proposals テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("proposals")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", proposal_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def list_proposals(
    company_id: str,
    *,
    opportunity_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """proposals テーブルから一覧取得する。"""
    q = (
        _db()
        .table("proposals")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if opportunity_id:
        q = q.eq("opportunity_id", opportunity_id)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_proposal(
    company_id: str,
    proposal_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """proposals テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("proposals")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", proposal_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# quotations
# ═══════════════════════════════════════════════════════════════════════════

async def create_quotation(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """quotations テーブルに INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "draft",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("quotations").insert(row).execute()
    return result.data[0]


async def get_quotation(company_id: str, quotation_id: str) -> dict[str, Any] | None:
    """quotations テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("quotations")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", quotation_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def update_quotation(
    company_id: str,
    quotation_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """quotations テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("quotations")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", quotation_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# contracts
# ═══════════════════════════════════════════════════════════════════════════

async def create_contract(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """contracts テーブルに INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "draft",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("contracts").insert(row).execute()
    return result.data[0]


async def get_contract(company_id: str, contract_id: str) -> dict[str, Any] | None:
    """contracts テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("contracts")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", contract_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def update_contract(
    company_id: str,
    contract_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """contracts テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("contracts")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", contract_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# customers
# ═══════════════════════════════════════════════════════════════════════════

async def create_customer(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """customers テーブルに INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "onboarding",
        "health_score": 100,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("customers").insert(row).execute()
    return result.data[0]


async def get_customer(company_id: str, customer_id: str) -> dict[str, Any] | None:
    """customers テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("customers")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", customer_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def list_customers(
    company_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """customers テーブルから一覧取得する。"""
    q = (
        _db()
        .table("customers")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if status:
        q = q.eq("status", status)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_customer(
    company_id: str,
    customer_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """customers テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("customers")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", customer_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# customer_health
# ═══════════════════════════════════════════════════════════════════════════

async def create_health_record(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """customer_health テーブルにヘルスレコードを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "recorded_at": _now_iso(),
        **data,
    }
    result = _db().table("customer_health").insert(row).execute()
    return result.data[0]


async def list_health_by_customer(
    company_id: str,
    customer_id: str,
    *,
    limit: int = 30,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """customer_health テーブルから顧客別に取得する。"""
    result = (
        _db()
        .table("customer_health")
        .select("*")
        .eq("company_id", company_id)
        .eq("customer_id", customer_id)
        .order("recorded_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data


# ═══════════════════════════════════════════════════════════════════════════
# revenue_records
# ═══════════════════════════════════════════════════════════════════════════

async def create_revenue(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """revenue_records テーブルに売上/入金レコードを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("revenue_records").insert(row).execute()
    return result.data[0]


async def list_revenue_by_period(
    company_id: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """revenue_records テーブルから期間指定で取得する。"""
    q = (
        _db()
        .table("revenue_records")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if start_date:
        q = q.gte("period", start_date)
    if end_date:
        q = q.lte("period", end_date)
    if customer_id:
        q = q.eq("customer_id", customer_id)

    q = q.order("period", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


# ═══════════════════════════════════════════════════════════════════════════
# feature_requests
# ═══════════════════════════════════════════════════════════════════════════

async def create_request(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """feature_requests テーブルに機能リクエストを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "open",
        "votes": 1,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("feature_requests").insert(row).execute()
    return result.data[0]


async def list_requests(
    company_id: str,
    *,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """feature_requests テーブルから一覧取得する。"""
    q = (
        _db()
        .table("feature_requests")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if status:
        q = q.eq("status", status)

    q = q.order("votes", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_request(
    company_id: str,
    request_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """feature_requests テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("feature_requests")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", request_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# support_tickets
# ═══════════════════════════════════════════════════════════════════════════

async def create_ticket(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """support_tickets テーブルにサポートチケットを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "status": "open",
        "priority": "medium",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        **data,
    }
    result = _db().table("support_tickets").insert(row).execute()
    return result.data[0]


async def get_ticket(company_id: str, ticket_id: str) -> dict[str, Any] | None:
    """support_tickets テーブルから 1 件取得する。"""
    result = (
        _db()
        .table("support_tickets")
        .select("*")
        .eq("company_id", company_id)
        .eq("id", ticket_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def list_tickets(
    company_id: str,
    *,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """support_tickets テーブルから一覧取得する。"""
    q = (
        _db()
        .table("support_tickets")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if status:
        q = q.eq("status", status)
    if customer_id:
        q = q.eq("customer_id", customer_id)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


async def update_ticket(
    company_id: str,
    ticket_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """support_tickets テーブルを更新する。"""
    update_data = {**_strip_none(data), "updated_at": _now_iso()}
    result = (
        _db()
        .table("support_tickets")
        .update(update_data)
        .eq("company_id", company_id)
        .eq("id", ticket_id)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


# ═══════════════════════════════════════════════════════════════════════════
# ticket_messages
# ═══════════════════════════════════════════════════════════════════════════

async def create_message(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """ticket_messages テーブルにメッセージを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("ticket_messages").insert(row).execute()
    return result.data[0]


async def list_messages_by_ticket(
    company_id: str,
    ticket_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """ticket_messages テーブルからチケット別に取得する。"""
    result = (
        _db()
        .table("ticket_messages")
        .select("*")
        .eq("company_id", company_id)
        .eq("ticket_id", ticket_id)
        .order("created_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data


# ═══════════════════════════════════════════════════════════════════════════
# win_loss_patterns
# ═══════════════════════════════════════════════════════════════════════════

async def create_pattern(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """win_loss_patterns テーブルにパターンを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("win_loss_patterns").insert(row).execute()
    return result.data[0]


async def list_patterns(
    company_id: str,
    *,
    outcome: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """win_loss_patterns テーブルから一覧取得する。"""
    q = (
        _db()
        .table("win_loss_patterns")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if outcome:
        q = q.eq("outcome", outcome)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


# ═══════════════════════════════════════════════════════════════════════════
# outreach_performance
# ═══════════════════════════════════════════════════════════════════════════

async def create_performance(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """outreach_performance テーブルにパフォーマンスレコードを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("outreach_performance").insert(row).execute()
    return result.data[0]


async def list_performance(
    company_id: str,
    *,
    industry: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    email_variant: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """outreach_performance テーブルからフィルタ付きで一覧取得する。"""
    q = (
        _db()
        .table("outreach_performance")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if industry:
        q = q.eq("industry", industry)
    if start_date:
        q = q.gte("period", start_date)
    if end_date:
        q = q.lte("period", end_date)
    if email_variant:
        q = q.eq("email_variant", email_variant)

    q = q.order("period", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


# ═══════════════════════════════════════════════════════════════════════════
# cs_feedback
# ═══════════════════════════════════════════════════════════════════════════

async def create_feedback(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """cs_feedback テーブルにフィードバックを INSERT する。"""
    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("cs_feedback").insert(row).execute()
    return result.data[0]


async def list_feedback(
    company_id: str,
    *,
    customer_id: Optional[str] = None,
    feedback_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """cs_feedback テーブルから一覧取得する。"""
    q = (
        _db()
        .table("cs_feedback")
        .select("*", count="exact")
        .eq("company_id", company_id)
    )
    if customer_id:
        q = q.eq("customer_id", customer_id)
    if feedback_type:
        q = q.eq("feedback_type", feedback_type)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    result = q.execute()
    return result.data, result.count or 0


# ═══════════════════════════════════════════════════════════════════════════
# scoring_model_versions
# ═══════════════════════════════════════════════════════════════════════════

async def get_active_model(company_id: str) -> dict[str, Any] | None:
    """scoring_model_versions テーブルからアクティブなモデルを取得する。"""
    result = (
        _db()
        .table("scoring_model_versions")
        .select("*")
        .eq("company_id", company_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    return result.data


async def create_model_version(company_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """scoring_model_versions テーブルに新バージョンを INSERT する。

    新バージョンを active にする場合、既存の active モデルを非活性化する。
    """
    if data.get("is_active", False):
        # 既存の active を全て非活性化
        _db().table("scoring_model_versions").update(
            {"is_active": False}
        ).eq("company_id", company_id).eq("is_active", True).execute()

    row = {
        "id": str(uuid4()),
        "company_id": company_id,
        "created_at": _now_iso(),
        **data,
    }
    result = _db().table("scoring_model_versions").insert(row).execute()
    return result.data[0]

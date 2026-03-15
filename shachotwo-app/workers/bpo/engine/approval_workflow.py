"""承認ワークフローエンジン"""
from datetime import datetime

from db.supabase import get_service_client as get_client


async def create_approval(
    company_id: str,
    target_type: str,
    target_id: str,
    requested_by: str,
) -> dict:
    """承認リクエストを作成"""
    client = get_client()
    result = client.table("bpo_approvals").insert({
        "company_id": company_id,
        "target_type": target_type,
        "target_id": target_id,
        "requested_by": requested_by,
        "status": "pending",
    }).execute()
    return result.data[0] if result.data else {}


async def approve(
    approval_id: str,
    approver_id: str,
    comment: str | None = None,
) -> dict:
    """承認"""
    client = get_client()
    update_data = {
        "approver_id": approver_id,
        "status": "approved",
        "decided_at": datetime.utcnow().isoformat(),
    }
    if comment:
        update_data["comment"] = comment
    result = client.table("bpo_approvals").update(
        update_data
    ).eq("id", approval_id).execute()
    return result.data[0] if result.data else {}


async def reject(
    approval_id: str,
    approver_id: str,
    comment: str | None = None,
) -> dict:
    """却下"""
    client = get_client()
    update_data = {
        "approver_id": approver_id,
        "status": "rejected",
        "decided_at": datetime.utcnow().isoformat(),
    }
    if comment:
        update_data["comment"] = comment
    result = client.table("bpo_approvals").update(
        update_data
    ).eq("id", approval_id).execute()
    return result.data[0] if result.data else {}


async def get_pending_approvals(
    company_id: str,
    target_type: str | None = None,
) -> list[dict]:
    """未承認の申請一覧を取得"""
    client = get_client()
    query = client.table("bpo_approvals").select("*").eq(
        "company_id", company_id
    ).eq("status", "pending")
    if target_type:
        query = query.eq("target_type", target_type)
    result = await query.order("requested_at", desc=True).execute()
    return result.data or []

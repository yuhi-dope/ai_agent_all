"""製造業BPO FastAPIルーター"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import logging

from auth.middleware import get_current_user
from workers.bpo.manufacturing.models import (
    MfgQuoteCreate, MfgQuoteResponse, MfgQuoteItemResponse,
    MfgQuoteItemUpdate, MfgFinalizeRequest, MfgFinalizeResponse,
    ChargeRateCreate, ChargeRateResponse, QuoteCostBreakdown,
)
from workers.bpo.manufacturing.quoting import QuotingPipeline
from db.supabase import get_service_client as get_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# 見積 CRUD
# ─────────────────────────────────────

@router.post("/quotes", response_model=MfgQuoteResponse)
async def create_quote(body: MfgQuoteCreate, user=Depends(get_current_user)):
    """新規見積作成（図面解析→工程推定→コスト計算を一括実行）"""
    pipeline = QuotingPipeline()

    # Step 1: 解析
    analysis = await pipeline.analyze_drawing_text(
        description=body.description,
        material=body.material,
        quantity=body.quantity,
        surface_treatment=body.surface_treatment,
    )

    # Step 2: 工程推定
    processes = await pipeline.estimate_processes(analysis)

    # Step 3: コスト計算
    costs = await pipeline.calculate_costs(
        analysis=analysis,
        processes=processes,
        quantity=body.quantity,
        company_id=str(user.company_id),
        overhead_rate=body.overhead_rate,
        profit_rate=body.profit_rate,
    )

    # Step 4: DB保存
    quote_id = await pipeline.save_quote(
        company_id=str(user.company_id),
        customer_name=body.customer_name,
        project_name=body.project_name,
        analysis=analysis,
        processes=processes,
        costs=costs,
        quantity=body.quantity,
        surface_treatment=body.surface_treatment,
        delivery_date=body.delivery_date,
        description=body.description,
    )

    # 保存した見積を取得して返す
    return await _get_quote_response(quote_id, str(user.company_id), costs)


@router.get("/quotes", response_model=list[MfgQuoteResponse])
async def list_quotes(
    status: Optional[str] = None,
    customer_name: Optional[str] = None,
    limit: int = Query(default=50, le=100),
    offset: int = 0,
    user=Depends(get_current_user),
):
    """見積一覧"""
    client = get_client()
    query = client.table("mfg_quotes").select("*").eq(
        "company_id", str(user.company_id)
    )
    if status:
        query = query.eq("status", status)
    if customer_name:
        query = query.ilike("customer_name", f"%{customer_name}%")

    result = query.order("created_at", desc=True).range(
        offset, offset + limit - 1
    ).execute()

    quotes = []
    for row in (result.data or []):
        quotes.append(MfgQuoteResponse(
            id=row["id"],
            quote_number=row["quote_number"],
            customer_name=row["customer_name"],
            project_name=row.get("project_name"),
            quantity=row["quantity"],
            material=row.get("material"),
            surface_treatment=row.get("surface_treatment"),
            shape_type=row.get("shape_type"),
            total_amount=row.get("total_amount"),
            profit_margin=row.get("profit_margin"),
            status=row["status"],
            created_at=row.get("created_at"),
        ))
    return quotes


@router.get("/quotes/{quote_id}", response_model=MfgQuoteResponse)
async def get_quote(quote_id: str, user=Depends(get_current_user)):
    """見積詳細（工程明細付き）"""
    return await _get_quote_response(quote_id, str(user.company_id))


@router.patch("/quotes/{quote_id}/items/{item_id}")
async def update_quote_item(
    quote_id: str,
    item_id: str,
    body: MfgQuoteItemUpdate,
    user=Depends(get_current_user),
):
    """工程の修正（段取り時間、サイクルタイム、チャージレート等）"""
    client = get_client()

    update_data = {"user_modified": True, "cost_source": "manual"}
    if body.setup_time_min is not None:
        update_data["setup_time_min"] = body.setup_time_min
    if body.cycle_time_min is not None:
        update_data["cycle_time_min"] = body.cycle_time_min
    if body.charge_rate is not None:
        update_data["charge_rate"] = body.charge_rate
    if body.notes is not None:
        update_data["notes"] = body.notes

    result = client.table("mfg_quote_items").update(update_data).eq(
        "id", item_id
    ).eq("company_id", str(user.company_id)).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Quote item not found")

    return {"message": "更新しました", "item_id": item_id}


@router.post("/quotes/{quote_id}/finalize", response_model=MfgFinalizeResponse)
async def finalize_quote(
    quote_id: str,
    body: MfgFinalizeRequest,
    user=Depends(get_current_user),
):
    """見積確定 + 学習"""
    client = get_client()
    company_id = str(user.company_id)

    # 見積の存在確認
    quote = client.table("mfg_quotes").select("id").eq(
        "id", quote_id
    ).eq("company_id", company_id).single().execute()
    if not quote.data:
        raise HTTPException(status_code=404, detail="Quote not found")

    finalized_count = 0
    actual_times = []

    for item in body.items:
        current = client.table("mfg_quote_items").select("*").eq(
            "id", item.item_id
        ).eq("company_id", company_id).single().execute()
        if not current.data:
            continue

        update_data = {"user_modified": True, "cost_source": "manual"}
        actual = {"item_id": item.item_id}

        if item.confirmed_setup_time is not None:
            update_data["setup_time_min"] = item.confirmed_setup_time
            actual["actual_setup_time_min"] = item.confirmed_setup_time
        if item.confirmed_cycle_time is not None:
            update_data["cycle_time_min"] = item.confirmed_cycle_time
            actual["actual_cycle_time_min"] = item.confirmed_cycle_time
        if item.confirmed_charge_rate is not None:
            update_data["charge_rate"] = item.confirmed_charge_rate

        client.table("mfg_quote_items").update(update_data).eq(
            "id", item.item_id
        ).eq("company_id", company_id).execute()
        actual_times.append(actual)
        finalized_count += 1

    # ステータスを確定に
    client.table("mfg_quotes").update({"status": "sent"}).eq(
        "id", quote_id
    ).eq("company_id", company_id).execute()

    # 学習
    pipeline = QuotingPipeline()
    learned = await pipeline.learn_from_actual(quote_id, company_id, actual_times)

    return MfgFinalizeResponse(
        finalized_count=finalized_count,
        learned_count=learned,
        accuracy_summary={
            "items_modified": finalized_count,
            "items_learned": learned,
        },
    )


# ─────────────────────────────────────
# チャージレート管理
# ─────────────────────────────────────

@router.get("/charge-rates", response_model=list[ChargeRateResponse])
async def list_charge_rates(user=Depends(get_current_user)):
    """チャージレート一覧"""
    client = get_client()
    result = client.table("mfg_charge_rates").select("*").eq(
        "company_id", str(user.company_id)
    ).order("equipment_name").execute()

    return [
        ChargeRateResponse(
            id=row["id"],
            equipment_name=row["equipment_name"],
            equipment_type=row["equipment_type"],
            charge_rate=int(row["charge_rate"]),
            setup_time_default=row.get("setup_time_default"),
            notes=row.get("notes"),
        )
        for row in (result.data or [])
    ]


@router.post("/charge-rates", response_model=ChargeRateResponse)
async def upsert_charge_rate(body: ChargeRateCreate, user=Depends(get_current_user)):
    """チャージレート登録/更新"""
    client = get_client()
    company_id = str(user.company_id)

    data = {
        "company_id": company_id,
        "equipment_name": body.equipment_name,
        "equipment_type": body.equipment_type,
        "charge_rate": body.charge_rate,
        "setup_time_default": body.setup_time_default,
        "notes": body.notes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    result = client.table("mfg_charge_rates").upsert(
        data, on_conflict="company_id,equipment_name"
    ).execute()

    row = result.data[0]
    return ChargeRateResponse(
        id=row["id"],
        equipment_name=row["equipment_name"],
        equipment_type=row["equipment_type"],
        charge_rate=int(row["charge_rate"]),
        setup_time_default=row.get("setup_time_default"),
        notes=row.get("notes"),
    )


# ─────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────

async def _get_quote_response(
    quote_id: str,
    company_id: str,
    costs: QuoteCostBreakdown | None = None,
) -> MfgQuoteResponse:
    client = get_client()

    quote = client.table("mfg_quotes").select("*").eq(
        "id", quote_id
    ).eq("company_id", company_id).single().execute()
    if not quote.data:
        raise HTTPException(status_code=404, detail="Quote not found")

    items_result = client.table("mfg_quote_items").select("*").eq(
        "quote_id", quote_id
    ).eq("company_id", company_id).order("sort_order").execute()

    items = [
        MfgQuoteItemResponse(
            id=row["id"],
            sort_order=row["sort_order"],
            process_name=row["process_name"],
            equipment=row.get("equipment"),
            equipment_type=row.get("equipment_type"),
            setup_time_min=row.get("setup_time_min"),
            cycle_time_min=row.get("cycle_time_min"),
            total_time_min=row.get("total_time_min"),
            charge_rate=int(row["charge_rate"]) if row.get("charge_rate") else None,
            process_cost=int(row["process_cost"]) if row.get("process_cost") else None,
            material_cost=int(row["material_cost"]) if row.get("material_cost") else None,
            outsource_cost=int(row["outsource_cost"]) if row.get("outsource_cost") else None,
            cost_source=row.get("cost_source", "ai_estimated"),
            confidence=row.get("confidence"),
            user_modified=row.get("user_modified", False),
            notes=row.get("notes"),
        )
        for row in (items_result.data or [])
    ]

    q = quote.data
    return MfgQuoteResponse(
        id=q["id"],
        quote_number=q["quote_number"],
        customer_name=q["customer_name"],
        project_name=q.get("project_name"),
        quantity=q["quantity"],
        material=q.get("material"),
        surface_treatment=q.get("surface_treatment"),
        shape_type=q.get("shape_type"),
        total_amount=q.get("total_amount"),
        profit_margin=q.get("profit_margin"),
        status=q["status"],
        items=items,
        cost_breakdown=costs,
        created_at=q.get("created_at"),
    )

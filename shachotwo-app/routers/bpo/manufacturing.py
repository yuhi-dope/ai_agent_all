"""製造業BPO FastAPIルーター"""
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import logging

from auth.middleware import get_current_user
from security.rate_limiter import check_rate_limit
from workers.bpo.manufacturing.models import (
    MfgQuoteCreate, MfgQuoteResponse, MfgQuoteItemResponse,
    MfgQuoteItemUpdate, MfgFinalizeRequest, MfgFinalizeResponse,
    ChargeRateCreate, ChargeRateResponse, QuoteCostBreakdown,
    HearingInput, QuoteResult,
)
from workers.bpo.manufacturing.quoting import QuotingPipeline
from workers.bpo.manufacturing.engine import ManufacturingQuotingEngine
from workers.bpo.manufacturing.plugins import list_plugins
from workers.bpo.manufacturing.pipelines import PIPELINE_REGISTRY
from workers.bpo.manufacturing.pipelines.production_planning_pipeline import (
    run_production_planning_pipeline,
)
from workers.bpo.manufacturing.pipelines.quality_control_pipeline import (
    run_quality_control_pipeline,
)
from workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline import (
    run_inventory_optimization_pipeline,
)
from workers.bpo.manufacturing.pipelines.sop_management_pipeline import (
    run_sop_management_pipeline,
)
from workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline import (
    run_equipment_maintenance_pipeline,
)
from workers.bpo.manufacturing.pipelines.procurement_pipeline import (
    run_procurement_pipeline,
)
from workers.bpo.manufacturing.pipelines.iso_document_pipeline import (
    run_iso_document_pipeline,
)
from db.supabase import get_service_client as get_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# 見積 CRUD
# ─────────────────────────────────────

@router.post("/quotes", response_model=MfgQuoteResponse)
async def create_quote(body: MfgQuoteCreate, user=Depends(get_current_user)):
    """新規見積作成（図面解析→工程推定→コスト計算を一括実行）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
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
    check_rate_limit(str(user.company_id), "bpo_pipeline")
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
# 3層エンジン（v2）
# ─────────────────────────────────────

@router.post("/quotes/v2", response_model=QuoteResult)
async def create_quote_v2(body: HearingInput, user=Depends(get_current_user)):
    """3層エンジンによる見積作成（Plugin → YAML → LLM の順で解決）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    hearing = body.model_copy(update={"company_id": str(user.company_id)})
    try:
        result = await ManufacturingQuotingEngine().run(hearing)
        return result
    except Exception as e:
        logger.exception("3層エンジン見積失敗")
        raise HTTPException(status_code=500, detail="見積処理中にエラーが発生しました")


@router.get("/plugins")
async def get_plugins(user=Depends(get_current_user)):
    """利用可能なプラグイン（業種別計算モジュール）の一覧"""
    try:
        plugins = list_plugins()
        return {"plugins": plugins}
    except Exception as e:
        logger.exception("プラグイン一覧取得失敗")
        raise HTTPException(status_code=500, detail="プラグイン一覧の取得に失敗しました")


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
    check_rate_limit(str(user.company_id), "bpo_pipeline")
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

# ─────────────────────────────────────
# 製造業BPOパイプライン（汎用）
# ─────────────────────────────────────

class PipelineRunRequest(BaseModel):
    input_data: dict[str, Any]
    options: dict[str, Any] = {}


@router.get("/pipelines")
async def list_pipelines(user=Depends(get_current_user)):
    """利用可能な製造業BPOパイプライン一覧"""
    return {
        "pipelines": [
            {
                "pipeline_id": k,
                "description": v["description"],
                "steps": v["steps"],
                "status": v["status"],
            }
            for k, v in PIPELINE_REGISTRY.items()
        ]
    }


@router.post("/pipelines/production_planning")
async def run_production_planning(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """生産計画AI（受注データ→山積み計算→ガントチャート→生産計画書）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_production_planning_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("production_planning_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/quality_control")
async def run_quality_control(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """品質管理（検査データ→SPC計算→不良予兆検知→品質月次レポート）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_quality_control_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("quality_control_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/inventory_optimization")
async def run_inventory_optimization(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """在庫最適化（ABC分析→安全在庫計算→発注点算出→発注推奨リスト）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_inventory_optimization_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("inventory_optimization_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/sop_management")
async def run_sop_management(
    body: PipelineRunRequest,
    existing_sop_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    """SOP管理（手順書作成→安全衛生法チェック→改訂管理→PDF出力）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_sop_management_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
            existing_sop_id=existing_sop_id or body.options.get("existing_sop_id"),
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("sop_management_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/equipment_maintenance")
async def run_equipment_maintenance(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """設備保全（MTBF/MTTR計算→保全期限アラート→月次保全カレンダー生成）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_equipment_maintenance_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
            target_month=body.options.get("target_month"),
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("equipment_maintenance_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/procurement")
async def run_procurement(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """仕入管理（BOM展開→MRP所要量計算→発注先選定→発注書生成）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_procurement_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("procurement_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pipelines/iso_document")
async def run_iso_document(
    body: PipelineRunRequest,
    user=Depends(get_current_user),
):
    """ISO文書管理（条項別チェック→有効期限確認→監査チェックリスト生成）"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    try:
        result = await run_iso_document_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
            iso_standard=body.options.get("iso_standard", "9001"),
            previous_audit_id=body.options.get("previous_audit_id"),
        )
        return {
            "success": result.success,
            "failed_step": result.failed_step,
            "total_cost_yen": result.total_cost_yen,
            "total_duration_ms": result.total_duration_ms,
            "steps": [
                {
                    "step_no": s.step_no,
                    "step_name": s.step_name,
                    "success": s.success,
                    "confidence": s.confidence,
                    "warning": s.warning,
                }
                for s in result.steps
            ],
            "final_output": result.final_output,
        }
    except Exception as e:
        logger.exception("iso_document_pipeline失敗")
        raise HTTPException(status_code=500, detail=str(e))


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

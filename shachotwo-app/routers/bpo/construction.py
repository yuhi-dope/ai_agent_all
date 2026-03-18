"""建設業BPO FastAPIルーター"""
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import io
import json as _json
import logging

from auth.middleware import get_current_user
from workers.bpo.construction.models import (
    EstimationProjectCreate, EstimationProjectResponse,
    EstimationItemCreate, EstimationItemResponse,
    ConstructionSiteCreate, ConstructionSiteResponse,
    WorkerCreate, WorkerResponse,
    WorkerQualificationCreate, WorkerQualificationResponse,
    SiteWorkerAssignment,
    ConstructionContractCreate, ConstructionContractResponse,
    ProgressRecordCreate, ProgressRecordResponse,
    CostRecordCreate,
)
from db.supabase import get_service_client as get_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# フィードバック学習ループ用モデル
# ─────────────────────────────────────

class FinalizeItem(BaseModel):
    item_id: str
    confirmed_unit_price: float


class FinalizeRequest(BaseModel):
    items: list[FinalizeItem]


class FinalizeResponse(BaseModel):
    finalized_count: int
    learned_prices_count: int
    accuracy_summary: dict


class AccuracyByCategory(BaseModel):
    category: str
    count: int
    avg_accuracy: float


class EstimationAccuracyResponse(BaseModel):
    total_finalized_items: int
    items_with_ai_price: int
    items_modified: int
    items_unchanged: int
    avg_accuracy_rate: float
    accuracy_by_category: list[AccuracyByCategory]
    unit_price_master_count: int
    learning_progress: str


class ExtractionFeedbackRequest(BaseModel):
    original_items: list[dict]
    corrected_items: list[dict]
    source_format: Optional[str] = None


class ExtractionFeedbackResponse(BaseModel):
    id: str
    diff_summary: dict


router = APIRouter()


# ─────────────────────────────────────
# 積算
# ─────────────────────────────────────

@router.post("/estimation/projects", response_model=EstimationProjectResponse)
async def create_estimation_project(
    body: EstimationProjectCreate,
    user=Depends(get_current_user),
):
    """積算プロジェクト作成"""
    client = get_client()
    result = client.table("estimation_projects").insert({
        "company_id": user.company_id,
        **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/estimation/projects")
async def list_estimation_projects(
    status: str | None = None,
    user=Depends(get_current_user),
):
    """積算プロジェクト一覧"""
    client = get_client()
    query = client.table("estimation_projects").select("*").eq(
        "company_id", user.company_id
    )
    if status:
        query = query.eq("status", status)
    result = query.order("created_at", desc=True).execute()
    return result.data or []


@router.get("/estimation/projects/{project_id}")
async def get_estimation_project(
    project_id: str,
    user=Depends(get_current_user),
):
    """積算プロジェクト詳細"""
    client = get_client()
    result = client.table("estimation_projects").select("*").eq(
        "id", project_id
    ).single().execute()
    return result.data


@router.patch("/estimation/projects/{project_id}")
async def update_estimation_project(
    project_id: str,
    body: dict,
    user=Depends(get_current_user),
):
    """積算プロジェクト更新"""
    client = get_client()
    result = client.table("estimation_projects").update(
        body
    ).eq("id", project_id).execute()
    return result.data[0] if result.data else {}


@router.post("/estimation/projects/{project_id}/extract")
async def extract_quantities(
    project_id: str,
    raw_text: str,
    user=Depends(get_current_user),
):
    """AI数量抽出"""
    from workers.bpo.construction.estimator import EstimationPipeline
    pipeline = EstimationPipeline()
    items = await pipeline.extract_quantities(
        project_id=project_id,
        company_id=user.company_id,
        raw_text=raw_text,
    )
    return {"extracted_count": len(items), "items": [i.model_dump() for i in items]}


@router.post("/estimation/projects/{project_id}/suggest-prices")
async def suggest_prices(
    project_id: str,
    user=Depends(get_current_user),
):
    """AI単価推定"""
    client = get_client()
    project = client.table("estimation_projects").select(
        "region, fiscal_year"
    ).eq("id", project_id).single().execute()

    from workers.bpo.construction.estimator import EstimationPipeline
    pipeline = EstimationPipeline()
    items = await pipeline.suggest_unit_prices(
        project_id=project_id,
        company_id=user.company_id,
        region=project.data["region"],
        fiscal_year=project.data["fiscal_year"],
    )
    return [i.model_dump() for i in items]


@router.post("/estimation/projects/{project_id}/calculate")
async def calculate_overhead(
    project_id: str,
    user=Depends(get_current_user),
):
    """諸経費計算"""
    client = get_client()
    project = client.table("estimation_projects").select(
        "project_type"
    ).eq("id", project_id).single().execute()

    from workers.bpo.construction.estimator import EstimationPipeline
    pipeline = EstimationPipeline()
    breakdown = await pipeline.calculate_overhead(
        project_id=project_id,
        company_id=user.company_id,
        project_type=project.data["project_type"],
    )
    return breakdown.model_dump()


@router.post("/estimation/projects/{project_id}/export")
async def export_breakdown(
    project_id: str,
    user=Depends(get_current_user),
):
    """内訳書Excel出力"""
    from workers.bpo.construction.estimator import EstimationPipeline
    from workers.bpo.engine.document_gen import ExcelGenerator

    pipeline = EstimationPipeline()
    data = await pipeline.generate_breakdown_data(project_id, user.company_id)
    excel_bytes = ExcelGenerator.generate_from_template(data)

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="breakdown_{project_id}.xlsx"'},
    )


@router.post("/estimation/projects/{project_id}/finalize", response_model=FinalizeResponse)
async def finalize_estimation(
    project_id: str,
    body: FinalizeRequest,
    user=Depends(get_current_user),
):
    """積算確定 + フィードバック学習ループ

    ユーザーが確定した単価でestimation_itemsを更新し、
    AI推定値との差分を記録してunit_price_masterに学習データを保存する。
    """
    client = get_client()

    project_result = client.table("estimation_projects").select(
        "id, company_id"
    ).eq("id", project_id).eq("company_id", user.company_id).single().execute()

    if not project_result.data:
        raise HTTPException(status_code=404, detail="Estimation project not found")

    finalized_at = datetime.now(timezone.utc).isoformat()

    accuracy_list: list[float] = []
    items_modified = 0
    items_unchanged = 0
    finalized_count = 0

    try:
        for finalize_item in body.items:
            current_result = client.table("estimation_items").select(
                "id, unit_price, company_id"
            ).eq("id", finalize_item.item_id).eq(
                "company_id", user.company_id
            ).single().execute()

            if not current_result.data:
                logger.warning(f"estimation_item not found: {finalize_item.item_id}")
                continue

            current = current_result.data
            original_ai_price = float(current["unit_price"]) if current["unit_price"] is not None else None
            confirmed_price = finalize_item.confirmed_unit_price

            is_modified = (
                original_ai_price is None
                or abs(original_ai_price - confirmed_price) > 0.01
            )

            if original_ai_price is not None and confirmed_price > 0:
                accuracy = 1.0 - abs(original_ai_price - confirmed_price) / confirmed_price
                accuracy = max(0.0, min(1.0, accuracy))
                accuracy_list.append(accuracy)

            if is_modified:
                items_modified += 1
            else:
                items_unchanged += 1

            finalize_meta = _json.dumps({
                "original_ai_price": original_ai_price,
                "user_modified": is_modified,
                "finalized_at": finalized_at,
            }, ensure_ascii=False)

            client.table("estimation_items").update({
                "unit_price": confirmed_price,
                "price_source": "manual",
                "notes": finalize_meta,
            }).eq("id", finalize_item.item_id).eq(
                "company_id", user.company_id
            ).execute()

            finalized_count += 1

        from workers.bpo.construction.estimator import EstimationPipeline
        pipeline = EstimationPipeline()
        learned_count = await pipeline.learn_from_result(
            project_id=project_id,
            company_id=user.company_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"finalize_estimation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    avg_accuracy = sum(accuracy_list) / len(accuracy_list) if accuracy_list else 0.0

    return FinalizeResponse(
        finalized_count=finalized_count,
        learned_prices_count=learned_count,
        accuracy_summary={
            "avg_accuracy": round(avg_accuracy, 4),
            "items_modified": items_modified,
            "items_unchanged": items_unchanged,
        },
    )


@router.get("/estimation/accuracy", response_model=EstimationAccuracyResponse)
async def get_estimation_accuracy(user=Depends(get_current_user)):
    """積算AIの推定精度ダッシュボード"""
    client = get_client()

    # finalized items（notesにJSON保存されたfinalized_at/user_modified/original_ai_price）
    items_result = client.table("estimation_items").select(
        "id, category, unit_price, notes"
    ).eq("company_id", user.company_id).not_.is_("notes", "null").execute()

    total_finalized = 0
    items_with_ai = 0
    items_modified = 0
    items_unchanged = 0
    accuracy_list: list[float] = []
    category_stats: dict[str, list[float]] = {}

    for item in (items_result.data or []):
        try:
            meta = _json.loads(item["notes"]) if isinstance(item["notes"], str) else item["notes"]
        except (ValueError, TypeError):
            continue
        if not meta.get("finalized_at"):
            continue

        total_finalized += 1
        ai_price = meta.get("original_ai_price")
        if ai_price is not None:
            items_with_ai += 1
            confirmed = float(item["unit_price"]) if item["unit_price"] else 0
            if confirmed > 0:
                acc = max(0.0, min(1.0, 1.0 - abs(ai_price - confirmed) / confirmed))
                accuracy_list.append(acc)
                cat = item.get("category", "不明")
                category_stats.setdefault(cat, []).append(acc)

        if meta.get("user_modified"):
            items_modified += 1
        else:
            items_unchanged += 1

    # unit_price_master count
    upm_result = client.table("unit_price_master").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    upm_count = upm_result.count or 0

    avg_acc = sum(accuracy_list) / len(accuracy_list) if accuracy_list else 0.0

    by_category = [
        AccuracyByCategory(
            category=cat,
            count=len(accs),
            avg_accuracy=round(sum(accs) / len(accs), 4),
        )
        for cat, accs in sorted(category_stats.items())
    ]

    if upm_count < 100:
        progress = "初期段階（100件未満）"
    elif upm_count < 500:
        progress = "学習中（100-499件）"
    else:
        progress = "安定期（500件以上）"

    return EstimationAccuracyResponse(
        total_finalized_items=total_finalized,
        items_with_ai_price=items_with_ai,
        items_modified=items_modified,
        items_unchanged=items_unchanged,
        avg_accuracy_rate=round(avg_acc, 4),
        accuracy_by_category=by_category,
        unit_price_master_count=upm_count,
        learning_progress=progress,
    )


@router.post(
    "/estimation/projects/{project_id}/extraction-feedback",
    response_model=ExtractionFeedbackResponse,
)
async def save_extraction_feedback(
    project_id: str,
    body: ExtractionFeedbackRequest,
    user=Depends(get_current_user),
):
    """数量抽出フィードバック（ユーザー修正内容）を保存"""
    client = get_client()

    # プロジェクト確認
    proj = client.table("estimation_projects").select("id").eq(
        "id", project_id
    ).eq("company_id", user.company_id).single().execute()
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    # diff計算
    orig_keys = {(i.get("category", ""), i.get("subcategory", ""), i.get("detail", "")) for i in body.original_items}
    corr_keys = {(i.get("category", ""), i.get("subcategory", ""), i.get("detail", "")) for i in body.corrected_items}

    added = len(corr_keys - orig_keys)
    deleted = len(orig_keys - corr_keys)
    common = orig_keys & corr_keys

    # 共通キーで数量・単価が変わったものをmodifiedとカウント
    orig_map = {(i.get("category", ""), i.get("subcategory", ""), i.get("detail", "")): i for i in body.original_items}
    corr_map = {(i.get("category", ""), i.get("subcategory", ""), i.get("detail", "")): i for i in body.corrected_items}
    modified = 0
    for key in common:
        o, c = orig_map[key], corr_map[key]
        if str(o.get("quantity")) != str(c.get("quantity")) or str(o.get("unit_price")) != str(c.get("unit_price")):
            modified += 1

    total = max(len(body.original_items), len(body.corrected_items), 1)
    accuracy = round(1.0 - (added + deleted + modified) / total, 4)

    diff_summary = {
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "total": total,
        "accuracy": accuracy,
    }

    result = client.table("extraction_feedback").insert({
        "company_id": user.company_id,
        "project_id": project_id,
        "original_items": body.original_items,
        "corrected_items": body.corrected_items,
        "diff_summary": diff_summary,
        "source_format": body.source_format,
    }).execute()

    # 正規化辞書の自動登録（工種名の変更を検出）
    for orig, corr in zip(body.original_items, body.corrected_items):
        for field in ("category", "subcategory", "detail"):
            o_val = (orig.get(field) or "").strip()
            c_val = (corr.get(field) or "").strip()
            if o_val and c_val and o_val != c_val:
                try:
                    # UPSERT: 既存ならoccurrence_countを+1
                    existing = client.table("term_normalization").select("id, occurrence_count").eq(
                        "company_id", user.company_id
                    ).eq("domain", "construction").eq("original_term", o_val).execute()

                    if existing.data:
                        client.table("term_normalization").update({
                            "normalized_term": c_val,
                            "occurrence_count": (existing.data[0].get("occurrence_count") or 0) + 1,
                        }).eq("id", existing.data[0]["id"]).execute()
                    else:
                        client.table("term_normalization").insert({
                            "company_id": user.company_id,
                            "domain": "construction",
                            "original_term": o_val,
                            "normalized_term": c_val,
                            "occurrence_count": 1,
                        }).execute()
                except Exception as e:
                    logger.warning(f"Failed to register normalization: {o_val} -> {c_val}: {e}")

    return ExtractionFeedbackResponse(
        id=result.data[0]["id"],
        diff_summary=diff_summary,
    )


@router.get("/estimation/labor-rates")
async def list_labor_rates(
    fiscal_year: int | None = None,
    region: str | None = None,
    user=Depends(get_current_user),
):
    """公共工事設計労務単価"""
    client = get_client()
    query = client.table("public_labor_rates").select("*")
    if fiscal_year:
        query = query.eq("fiscal_year", fiscal_year)
    if region:
        query = query.eq("region", region)
    result = query.execute()
    return result.data or []


# ─────────────────────────────────────
# 現場管理
# ─────────────────────────────────────

@router.post("/sites", response_model=ConstructionSiteResponse)
async def create_site(body: ConstructionSiteCreate, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_sites").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/sites")
async def list_sites(status: str | None = None, user=Depends(get_current_user)):
    client = get_client()
    query = client.table("construction_sites").select("*").eq("company_id", user.company_id)
    if status:
        query = query.eq("status", status)
    result = query.order("created_at", desc=True).execute()
    return result.data or []


@router.get("/sites/{site_id}")
async def get_site(site_id: str, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_sites").select("*").eq("id", site_id).single().execute()
    return result.data


@router.post("/sites/{site_id}/workers")
async def assign_worker(site_id: str, body: SiteWorkerAssignment, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("site_worker_assignments").insert({
        "site_id": site_id, "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0] if result.data else {}


# ─────────────────────────────────────
# 安全書類
# ─────────────────────────────────────

@router.post("/sites/{site_id}/safety-docs/worker-roster")
async def generate_worker_roster(site_id: str, user=Depends(get_current_user)):
    """作業員名簿生成"""
    from workers.bpo.construction.safety_docs import SafetyDocumentGenerator
    gen = SafetyDocumentGenerator()
    excel_bytes = await gen.generate_worker_roster(site_id, user.company_id)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="worker_roster_{site_id}.xlsx"'},
    )


@router.post("/sites/{site_id}/safety-docs/qualification-list")
async def generate_qualification_list(site_id: str, user=Depends(get_current_user)):
    """有資格者一覧表生成"""
    from workers.bpo.construction.safety_docs import SafetyDocumentGenerator
    gen = SafetyDocumentGenerator()
    excel_bytes = await gen.generate_qualification_list(site_id, user.company_id)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="qualification_list_{site_id}.xlsx"'},
    )


@router.get("/workers/expiring-qualifications")
async def expiring_qualifications(
    days_ahead: int = 90,
    user=Depends(get_current_user),
):
    """資格有効期限アラート"""
    from workers.bpo.construction.safety_docs import SafetyDocumentGenerator
    gen = SafetyDocumentGenerator()
    results = await gen.check_expiring_qualifications(user.company_id, days_ahead)
    return [r.model_dump() for r in results]


# ─────────────────────────────────────
# 作業員
# ─────────────────────────────────────

@router.post("/workers", response_model=WorkerResponse)
async def create_worker(body: WorkerCreate, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_workers").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/workers")
async def list_workers(user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_workers").select("*").eq(
        "company_id", user.company_id
    ).eq("status", "active").order("last_name").execute()
    return result.data or []


@router.get("/workers/{worker_id}")
async def get_worker(worker_id: str, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_workers").select(
        "*, worker_qualifications(*)"
    ).eq("id", worker_id).single().execute()
    return result.data


@router.post("/workers/{worker_id}/qualifications")
async def add_qualification(
    worker_id: str, body: WorkerQualificationCreate, user=Depends(get_current_user),
):
    client = get_client()
    result = client.table("worker_qualifications").insert({
        "worker_id": worker_id, "company_id": user.company_id,
        **body.model_dump(mode="json"),
    }).execute()
    return result.data[0] if result.data else {}


# ─────────────────────────────────────
# 出来高・請求
# ─────────────────────────────────────

@router.post("/contracts", response_model=ConstructionContractResponse)
async def create_contract(body: ConstructionContractCreate, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_contracts").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/contracts")
async def list_contracts(user=Depends(get_current_user)):
    client = get_client()
    result = client.table("construction_contracts").select("*").eq(
        "company_id", user.company_id
    ).order("created_at", desc=True).execute()
    return result.data or []


@router.post("/contracts/{contract_id}/progress")
async def create_progress(
    contract_id: str, body: ProgressRecordCreate, user=Depends(get_current_user),
):
    from workers.bpo.construction.billing import BillingEngine
    engine = BillingEngine()
    result = await engine.calculate_progress(
        contract_id=contract_id,
        company_id=user.company_id,
        period_year=body.period_year,
        period_month=body.period_month,
        items=[i.model_dump() for i in body.items],
    )
    return result


@router.post("/contracts/{contract_id}/progress/{progress_id}/invoice")
async def generate_invoice(
    contract_id: str, progress_id: str, user=Depends(get_current_user),
):
    from workers.bpo.construction.billing import BillingEngine
    engine = BillingEngine()
    excel_bytes = await engine.generate_invoice(progress_id, user.company_id)
    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="invoice_{progress_id}.xlsx"'},
    )


# ─────────────────────────────────────
# 原価管理
# ─────────────────────────────────────

@router.post("/costs")
async def create_cost_record(body: CostRecordCreate, user=Depends(get_current_user)):
    client = get_client()
    result = client.table("cost_records").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0] if result.data else {}


@router.get("/costs/report/{contract_id}")
async def get_cost_report(contract_id: str, user=Depends(get_current_user)):
    from workers.bpo.construction.billing import CostReportEngine
    engine = CostReportEngine()
    return await engine.generate_monthly_report(
        contract_id=contract_id,
        company_id=user.company_id,
        year=2026, month=3,
    )

"""建設業BPO FastAPIルーター"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
import io

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
    result = await client.table("estimation_projects").insert({
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
    result = await query.order("created_at", desc=True).execute()
    return result.data or []


@router.get("/estimation/projects/{project_id}")
async def get_estimation_project(
    project_id: str,
    user=Depends(get_current_user),
):
    """積算プロジェクト詳細"""
    client = get_client()
    result = await client.table("estimation_projects").select("*").eq(
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
    result = await client.table("estimation_projects").update(
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
    project = await client.table("estimation_projects").select(
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
    project = await client.table("estimation_projects").select(
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
    result = await query.execute()
    return result.data or []


# ─────────────────────────────────────
# 現場管理
# ─────────────────────────────────────

@router.post("/sites", response_model=ConstructionSiteResponse)
async def create_site(body: ConstructionSiteCreate, user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("construction_sites").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/sites")
async def list_sites(status: str | None = None, user=Depends(get_current_user)):
    client = get_client()
    query = client.table("construction_sites").select("*").eq("company_id", user.company_id)
    if status:
        query = query.eq("status", status)
    result = await query.order("created_at", desc=True).execute()
    return result.data or []


@router.get("/sites/{site_id}")
async def get_site(site_id: str, user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("construction_sites").select("*").eq("id", site_id).single().execute()
    return result.data


@router.post("/sites/{site_id}/workers")
async def assign_worker(site_id: str, body: SiteWorkerAssignment, user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("site_worker_assignments").insert({
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
    result = await client.table("construction_workers").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/workers")
async def list_workers(user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("construction_workers").select("*").eq(
        "company_id", user.company_id
    ).eq("status", "active").order("last_name").execute()
    return result.data or []


@router.get("/workers/{worker_id}")
async def get_worker(worker_id: str, user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("construction_workers").select(
        "*, worker_qualifications(*)"
    ).eq("id", worker_id).single().execute()
    return result.data


@router.post("/workers/{worker_id}/qualifications")
async def add_qualification(
    worker_id: str, body: WorkerQualificationCreate, user=Depends(get_current_user),
):
    client = get_client()
    result = await client.table("worker_qualifications").insert({
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
    result = await client.table("construction_contracts").insert({
        "company_id": user.company_id, **body.model_dump(mode="json"),
    }).execute()
    return result.data[0]


@router.get("/contracts")
async def list_contracts(user=Depends(get_current_user)):
    client = get_client()
    result = await client.table("construction_contracts").select("*").eq(
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
    result = await client.table("cost_records").insert({
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

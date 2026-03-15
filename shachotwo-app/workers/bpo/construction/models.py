"""建設業BPO Pydanticモデル"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field


# --- Enums ---

class ProjectType(str, Enum):
    PUBLIC_CIVIL = "public_civil"
    PUBLIC_BUILDING = "public_building"
    PRIVATE_CIVIL = "private_civil"
    PRIVATE_BUILDING = "private_building"


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    SUBMITTED = "submitted"
    WON = "won"
    LOST = "lost"


class PriceSource(str, Enum):
    MANUAL = "manual"
    PAST_RECORD = "past_record"
    LABOR_RATE = "labor_rate"
    MARKET_PRICE = "market_price"
    AI_ESTIMATED = "ai_estimated"


class SiteStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    COMPLETED = "completed"


# --- 積算 ---

class EstimationProjectCreate(BaseModel):
    name: str
    project_type: ProjectType
    region: str
    municipality: str | None = None
    fiscal_year: int
    client_name: str | None = None
    design_amount: int | None = None
    overhead_rates: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class EstimationProjectResponse(EstimationProjectCreate):
    id: str
    company_id: str
    estimated_amount: int | None = None
    status: ProjectStatus = ProjectStatus.DRAFT
    created_at: datetime
    updated_at: datetime


class EstimationItemCreate(BaseModel):
    sort_order: int
    category: str
    subcategory: str | None = None
    detail: str | None = None
    specification: str | None = None
    quantity: Decimal
    unit: str
    unit_price: Decimal | None = None
    price_source: PriceSource | None = None
    price_confidence: Decimal | None = None
    source_document: str | None = None
    notes: str | None = None


class EstimationItemResponse(EstimationItemCreate):
    id: str
    project_id: str
    company_id: str
    amount: int | None = None
    created_at: datetime


class EstimationItemWithPrice(EstimationItemResponse):
    """単価候補付きの積算明細"""
    price_candidates: list[dict] = Field(default_factory=list)


class OverheadBreakdown(BaseModel):
    direct_cost: int
    common_temporary: int          # 共通仮設費
    common_temporary_rate: Decimal
    site_management: int           # 現場管理費
    site_management_rate: Decimal
    general_admin: int             # 一般管理費等
    general_admin_rate: Decimal
    total: int


class IngestionResult(BaseModel):
    document_count: int
    extracted_items: int
    warnings: list[str] = Field(default_factory=list)


# --- 現場・作業員 ---

class ConstructionSiteCreate(BaseModel):
    name: str
    address: str | None = None
    client_name: str | None = None
    contract_amount: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    site_manager_name: str | None = None
    safety_officer_name: str | None = None
    green_file_format: str = "zenken"
    metadata: dict = Field(default_factory=dict)


class ConstructionSiteResponse(ConstructionSiteCreate):
    id: str
    company_id: str
    status: SiteStatus = SiteStatus.ACTIVE
    created_at: datetime
    updated_at: datetime


class WorkerCreate(BaseModel):
    last_name: str
    first_name: str
    last_name_kana: str | None = None
    first_name_kana: str | None = None
    birth_date: date | None = None
    blood_type: str | None = None
    address: str | None = None
    phone: str | None = None
    hire_date: date | None = None
    experience_years: int | None = None
    health_check_date: date | None = None
    health_check_result: str | None = None
    social_insurance: dict = Field(default_factory=dict)
    emergency_contact: dict = Field(default_factory=dict)


class WorkerResponse(WorkerCreate):
    id: str
    company_id: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class WorkerQualificationCreate(BaseModel):
    qualification_type: str   # license / special_training / skill_training
    qualification_name: str
    certificate_number: str | None = None
    issued_date: date | None = None
    expiry_date: date | None = None
    issuer: str | None = None
    certificate_image_url: str | None = None


class WorkerQualificationResponse(WorkerQualificationCreate):
    id: str
    worker_id: str
    company_id: str
    created_at: datetime


class SiteWorkerAssignment(BaseModel):
    worker_id: str
    entry_date: date
    exit_date: date | None = None
    role: str | None = None
    entry_education_date: date | None = None


# --- 安全書類 ---

class SafetyDocumentResponse(BaseModel):
    id: str
    site_id: str
    company_id: str
    document_type: str
    document_number: str | None = None
    version: int = 1
    file_url: str | None = None
    status: str = "draft"
    created_at: datetime
    updated_at: datetime


class ExpiringQualification(BaseModel):
    worker_id: str
    worker_name: str
    qualification_name: str
    expiry_date: date
    days_until_expiry: int


# --- 出来高・請求 ---

class ConstructionContractCreate(BaseModel):
    contract_number: str | None = None
    client_name: str
    project_name: str
    contract_amount: int
    tax_rate: Decimal = Decimal("0.10")
    contract_date: date | None = None
    start_date: date | None = None
    completion_date: date | None = None
    payment_terms: str | None = None
    billing_type: str = "monthly"
    items: list[dict]
    site_id: str | None = None


class ConstructionContractResponse(ConstructionContractCreate):
    id: str
    company_id: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class ProgressInput(BaseModel):
    item_name: str
    contract_amount: int
    progress_rate: Decimal  # 0.00 - 1.00
    progress_amount: int


class ProgressRecordCreate(BaseModel):
    period_year: int
    period_month: int
    items: list[ProgressInput]
    notes: str | None = None


class ProgressRecordResponse(BaseModel):
    id: str
    contract_id: str
    company_id: str
    period_year: int
    period_month: int
    items: list[dict]
    cumulative_amount: int
    previous_cumulative: int
    current_amount: int | None = None
    status: str = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None
    notes: str | None = None
    created_at: datetime


# --- 原価 ---

class CostRecordCreate(BaseModel):
    contract_id: str
    record_date: date
    cost_type: str  # material / labor / subcontract / equipment / overhead
    description: str
    amount: int
    vendor_name: str | None = None
    invoice_ref: str | None = None


class CostReportSummary(BaseModel):
    contract_id: str
    project_name: str
    contract_amount: int
    total_cost: int
    profit: int
    profit_rate: Decimal
    cost_by_type: dict[str, int]
    budget_vs_actual: list[dict]

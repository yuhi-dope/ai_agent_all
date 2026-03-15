"""BPO共通Pydanticモデル"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class BPOInvoiceItem(BaseModel):
    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal
    amount: int


class BPOInvoiceCreate(BaseModel):
    invoice_date: date
    due_date: date
    client_name: str
    subtotal: int
    tax_rate: Decimal = Decimal("0.10")
    tax_amount: int
    total: int
    items: list[BPOInvoiceItem]
    source_type: str | None = None
    source_id: str | None = None
    notes: str | None = None


class BPOInvoiceResponse(BPOInvoiceCreate):
    id: str
    company_id: str
    invoice_number: str
    status: str = "draft"
    file_url: str | None = None
    sent_at: datetime | None = None
    paid_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class BPOExpenseCreate(BaseModel):
    expense_date: date
    category: str
    description: str
    amount: int
    tax_included: bool = True
    receipt_url: str | None = None
    account_code: str | None = None
    cost_center: str | None = None


class BPOExpenseResponse(BPOExpenseCreate):
    id: str
    company_id: str
    user_id: str
    approval_status: str = "pending"
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime


class BPOVendorCreate(BaseModel):
    name: str
    vendor_type: str
    representative: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    payment_terms: str | None = None
    license_info: dict = Field(default_factory=dict)
    notes: str | None = None


class BPOVendorResponse(BPOVendorCreate):
    id: str
    company_id: str
    bank_info: dict = Field(default_factory=dict)
    evaluation: dict = Field(default_factory=dict)
    evaluation_date: date | None = None
    status: str = "active"
    industry_data: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class BPOPermitCreate(BaseModel):
    permit_type: str
    permit_name: str
    permit_number: str | None = None
    issued_date: date | None = None
    expiry_date: date | None = None
    renewal_lead_days: int = 180
    required_documents: list[str] = Field(default_factory=list)
    notes: str | None = None


class BPOPermitResponse(BPOPermitCreate):
    id: str
    company_id: str
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class BPOApprovalCreate(BaseModel):
    target_type: str
    target_id: str
    comment: str | None = None


class BPOApprovalResponse(BPOApprovalCreate):
    id: str
    company_id: str
    requested_by: str
    approver_id: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime
    decided_at: datetime | None = None

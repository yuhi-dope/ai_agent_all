"""契約AI Pydanticモデル"""

from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel


class PlanInfo(BaseModel):
    name: str  # starter / growth / bpo_fixed / engineer_fixed
    monthly_amount: int
    description: str
    features: list[str] = []


class EstimateItem(BaseModel):
    name: str
    quantity: int = 1
    unit_price: int
    amount: int


class EstimateData(BaseModel):
    estimate_number: str
    issue_date: date
    valid_until: date
    company_name: str
    contact_name: str = ""
    plan: PlanInfo
    items: list[EstimateItem]
    subtotal: int
    tax: int
    total: int


class ContractData(BaseModel):
    company_name: str
    representative: str
    address: str
    plan_name: str
    monthly_amount: int
    start_date: date
    signing_date: date | None = None


class Contract(BaseModel):
    id: str = ""
    deal_id: str = ""
    company_id: str = ""
    plan: str = ""
    monthly_amount: int = 0
    status: str = "estimate_sent"
    cloudsign_document_id: str = ""
    stripe_customer_id: str = ""
    stripe_subscription_id: str = ""
    payment_method: str = "credit_card"
    signed_at: datetime | None = None
    account_created_at: datetime | None = None
    onboarding_status: str = "pending"


class ContractEvent(BaseModel):
    id: str = ""
    contract_id: str
    event_type: str
    metadata: dict = {}


class LostReason(BaseModel):
    id: str = ""
    deal_id: str
    company_id: str = ""
    reason_category: str  # budget / timing / feature / competitor / other
    reason_detail: str = ""
    competitor_name: str = ""
    retry_date: date | None = None

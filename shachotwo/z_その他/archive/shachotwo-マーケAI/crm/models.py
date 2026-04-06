"""CRM Pydanticモデル"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class Company(BaseModel):
    id: str = ""
    name: str
    industry: str
    website_url: str = ""
    employee_count: int | None = None
    prefecture: str = ""
    city: str = ""
    corporate_number: str = ""
    pain_points: list[str] = []
    lp_url: str = ""
    created_at: datetime | None = None


class Lead(BaseModel):
    id: str = ""
    company_id: str
    contact_name: str
    phone: str
    email: str = ""
    source: str = "lp_cta"
    temperature: str = "hot"
    status: str = "new"
    created_at: datetime | None = None


class OutreachLog(BaseModel):
    id: str = ""
    company_id: str
    channel: str  # form / email / phone
    action: str  # sent / opened / clicked / replied / bounced
    subject: str = ""
    body_preview: str = ""
    sent_at: datetime | None = None


class PageView(BaseModel):
    id: str = ""
    company_id: str
    page_url: str
    duration_sec: int = 0
    cta_clicked: bool = False
    doc_downloaded: bool = False


class Deal(BaseModel):
    id: str = ""
    lead_id: str = ""
    company_id: str
    stage: str = "appointment"
    meeting_date: datetime | None = None
    meeting_url: str = ""
    proposed_plan: str = ""
    monthly_amount: int = 0

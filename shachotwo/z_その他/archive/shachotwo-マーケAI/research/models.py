"""企業リサーチ Pydanticモデル"""

from __future__ import annotations

from pydantic import BaseModel


class JobPosting(BaseModel):
    title: str
    occupation: str = ""
    salary_range: str = ""
    is_urgent: bool = False
    tags: list[str] = []


class PainPoint(BaseModel):
    category: str  # staffing / process / cost / compliance / growth
    detail: str
    evidence: str  # 推定根拠
    appeal_message: str  # 訴求ポイント


class CompanyResearch(BaseModel):
    name: str
    website_url: str = ""
    industry: str = ""
    employee_count: int | None = None
    capital: int | None = None
    representative: str = ""
    corporate_number: str = ""
    prefecture: str = ""
    city: str = ""
    address: str = ""
    establishment_year: int | None = None
    business_overview: str = ""
    job_postings: list[JobPosting] = []
    pain_points: list[PainPoint] = []
    raw_html: str = ""  # HP生テキスト（LLM入力用）

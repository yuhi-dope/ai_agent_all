"""gBizINFO API連携 — 法人番号・業種・従業員数・代表者名を取得"""

from __future__ import annotations

import httpx

from config import settings
from research.models import CompanyResearch

BASE_URL = "https://info.gbiz.go.jp/hojin/v1/hojin"


async def search_company(name: str) -> list[dict]:
    """企業名でgBizINFO検索"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            BASE_URL,
            params={"name": name, "limit": 5},
            headers={"X-hojinInfo-api-token": settings.gbizinfo_api_token},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("hojin-infos", [])


async def get_company_detail(corporate_number: str) -> dict:
    """法人番号から企業詳細を取得"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{BASE_URL}/{corporate_number}",
            headers={"X-hojinInfo-api-token": settings.gbizinfo_api_token},
        )
        resp.raise_for_status()
        return resp.json().get("hojin-infos", [{}])[0]


def map_to_company_research(gbiz_data: dict) -> CompanyResearch:
    """gBizINFOレスポンスをCompanyResearchにマッピング"""
    return CompanyResearch(
        name=gbiz_data.get("name", ""),
        corporate_number=gbiz_data.get("corporate_number", ""),
        industry=gbiz_data.get("business_items", [""])[0] if gbiz_data.get("business_items") else "",
        employee_count=gbiz_data.get("employee_number"),
        capital=gbiz_data.get("capital_stock"),
        representative=gbiz_data.get("representative_name", ""),
        prefecture=gbiz_data.get("prefecture", ""),
        city=gbiz_data.get("city", ""),
        address=gbiz_data.get("location", ""),
        establishment_year=gbiz_data.get("date_of_establishment"),
        business_overview=gbiz_data.get("business_summary", ""),
        website_url=gbiz_data.get("company_url", ""),
    )

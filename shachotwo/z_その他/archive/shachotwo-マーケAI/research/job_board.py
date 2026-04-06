"""求人サイトから企業リスト自動収集"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from research.models import CompanyResearch, JobPosting


async def search_indeed(industry: str, region: str, max_pages: int = 3) -> list[CompanyResearch]:
    """Indeedから求人企業を収集"""
    results: list[CompanyResearch] = []
    query = f"{industry} {region}"

    async with httpx.AsyncClient(timeout=15) as client:
        for page in range(max_pages):
            url = f"https://jp.indeed.com/jobs?q={query}&start={page * 10}"
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
            except httpx.HTTPError:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            for card in soup.select("[data-jk]"):
                company_el = card.select_one("[data-testid='company-name']")
                title_el = card.select_one("h2")
                if not company_el:
                    continue

                company_name = company_el.get_text(strip=True)
                job_title = title_el.get_text(strip=True) if title_el else ""

                # 重複チェック
                if any(r.name == company_name for r in results):
                    existing = next(r for r in results if r.name == company_name)
                    existing.job_postings.append(JobPosting(title=job_title, occupation=job_title))
                    continue

                results.append(CompanyResearch(
                    name=company_name,
                    industry=industry,
                    job_postings=[JobPosting(title=job_title, occupation=job_title)],
                ))

    return results


async def search_jobs(industry: str, region: str) -> list[CompanyResearch]:
    """全ソースから企業を収集（初期はIndeedのみ）"""
    results = await search_indeed(industry, region)
    # TODO: マイナビ、リクナビ、engage、求人ボックス、doda を追加
    return results

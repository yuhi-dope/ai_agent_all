"""企業HPスクレイピング"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from research.models import CompanyResearch


async def fetch_company_page(url: str, proxy: str | None = None) -> str:
    """企業HPのHTMLを取得"""
    transport = httpx.AsyncHTTPTransport(proxy=proxy) if proxy else None
    async with httpx.AsyncClient(transport=transport, timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return resp.text


def extract_company_info(html: str) -> dict:
    """HTMLから企業基本情報を抽出"""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    return {
        "title": soup.title.string if soup.title else "",
        "text_content": text[:5000],  # LLM入力用に先頭5000文字
    }


def find_contact_form(html: str, base_url: str) -> str | None:
    """HPから問い合わせフォームのURLを検出"""
    soup = BeautifulSoup(html, "lxml")
    keywords = ["問い合わせ", "お問い合わせ", "contact", "inquiry", "相談"]
    for a in soup.find_all("a", href=True):
        text = (a.get_text() + " " + a.get("href", "")).lower()
        if any(kw in text for kw in keywords):
            href = a["href"]
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                return base_url.rstrip("/") + href
    return None

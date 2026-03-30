"""企業名からWebを検索してHPのURLを特定するマイクロエージェント。

Brave Search HTML版を使い、API不要・無料で企業HPを特定する。
gBizINFO詳細APIで取得できないHP情報を補完するために使用する。
"""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# Serper.dev API（Google検索結果をJSON APIで返す。無料2,500クエリ）
SERPER_API_URL = "https://google.serper.dev/search"

# 除外するドメイン（企業HPではないサイト）
EXCLUDED_DOMAINS: frozenset[str] = frozenset({
    # SNS
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "tiktok.com",
    # 百科・EC・グルメ
    "wikipedia.org", "amazon.co.jp", "rakuten.co.jp",
    "tabelog.com", "hotpepper.jp",
    # 求人
    "indeed.com", "recruit.co.jp", "doda.jp", "mynavi.jp",
    "rikunabi.com", "en-japan.com", "hellowork.go.jp",
    # 検索エンジン・ポータル
    "google.com", "yahoo.co.jp", "bing.com",
    # 信用調査・企業DB
    "tdb.co.jp", "tsr-net.co.jp", "baseconnect.in",
    "gbiz.go.jp", "houjin-bangou.nta.go.jp",
    # 地図
    "maps.google.com", "goo.gl",
    # ブログ
    "ameblo.jp", "note.com", "livedoor.jp", "fc2.com",
    # 行政
    "go.jp",
    # ニュース
    "prnews.jp", "prwire.jp", "dreamnews.jp",
})

# リクエストヘッダ（ブラウザ偽装でボットブロック回避）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # brotli除外（デコードエラー回避）
}


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _extract_actual_url(ddg_href: str) -> Optional[str]:
    """DuckDuckGoのリダイレクトURLから実際のURLを抽出する。

    DuckDuckGo HTML版は href が以下の2形式になる:
      (a) //duckduckgo.com/l/?uddg=https%3A%2F%2F...&rut=...
      (b) 直接 https://... の場合もある

    Args:
        ddg_href: DuckDuckGo検索結果の <a href="..."> 値

    Returns:
        実際のURL文字列（抽出できなければ None）
    """
    if not ddg_href:
        return None

    # (a) uddg= パラメータに実URLが入っている
    m = _DDG_UDDG_RE.search(ddg_href)
    if m:
        try:
            return unquote(m.group(1))
        except Exception:
            return None

    # (b) 直接 https:// の場合
    if ddg_href.startswith("http://") or ddg_href.startswith("https://"):
        return ddg_href

    # (c) // から始まるプロトコル相対URL
    if ddg_href.startswith("//"):
        return "https:" + ddg_href

    return None


def _is_likely_company_website(url: str) -> bool:
    """URLが企業の公式HPらしいかを判定する。

    除外ドメインに該当しなければ true とする。
    ドメイン名と企業名の一致チェックは省略（多言語・略称に対応できないため）。

    Args:
        url: 判定対象のURL

    Returns:
        企業HP候補なら True
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if not netloc:
            return False

        # www. を除いたドメイン部分
        domain = netloc.lstrip("www.")

        for excluded in EXCLUDED_DOMAINS:
            # 完全一致 or サブドメイン一致（.go.jp など末尾パターン）
            if domain == excluded or domain.endswith("." + excluded):
                return False

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# パブリック API
# ---------------------------------------------------------------------------

# Google Custom Search API
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Serper API の残りクエリ数を追跡（プロセス内カウンタ）
_serper_call_count = 0
_SERPER_FREE_LIMIT = 2400  # 2500の手前で切り替え


async def _search_serper(query: str, api_key: str, timeout: float) -> Optional[str]:
    """Serper.dev APIで検索。"""
    global _serper_call_count
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                SERPER_API_URL,
                json={"q": query, "gl": "jp", "hl": "ja", "num": 5},
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            _serper_call_count += 1
            if resp.status_code == 429 or resp.status_code == 403:
                logger.warning(f"[serper] 枠切れ (HTTP {resp.status_code})。Google CSEに切替")
                return None
            if resp.status_code != 200:
                return None
            for result in resp.json().get("organic", []):
                url = result.get("link", "")
                if url and _is_likely_company_website(url):
                    return url
    except Exception as e:
        logger.debug(f"[serper] エラー: {e}")
    return None


async def _search_google_cse(query: str, api_key: str, cx: str, timeout: float) -> Optional[str]:
    """Google Custom Search APIで検索。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                GOOGLE_CSE_URL,
                params={"key": api_key, "cx": cx, "q": query, "num": 5, "gl": "jp", "hl": "ja"},
            )
            if resp.status_code != 200:
                logger.debug(f"[google_cse] HTTP {resp.status_code}")
                return None
            for item in resp.json().get("items", []):
                url = item.get("link", "")
                if url and _is_likely_company_website(url):
                    return url
    except Exception as e:
        logger.debug(f"[google_cse] エラー: {e}")
    return None


async def search_company_website(
    company_name: str,
    location: str = "",
    timeout: float = 10.0,
) -> Optional[str]:
    """企業名でWeb検索してHPのURLを返す。

    Serper.dev API → 枠切れ時に Google Custom Search API へ自動フォールバック。

    Args:
        company_name: 企業名
        location:     所在地（都道府県を検索に追加）
        timeout:      HTTPタイムアウト（秒）

    Returns:
        HPのURL、または None
    """
    import os

    if location:
        pref = location[:3]
        query = f"{company_name} {pref}"
    else:
        query = company_name

    # --- Serper（無料2,500回）を優先 ---
    serper_key = os.environ.get("SERPER_API_KEY", "")
    if serper_key and _serper_call_count < _SERPER_FREE_LIMIT:
        result = await _search_serper(query, serper_key, timeout)
        if result:
            return result

    # --- Google CSE にフォールバック ---
    gcs_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    gcs_cx = os.environ.get("GOOGLE_CSE_CX", "")
    if gcs_key and gcs_cx:
        if _serper_call_count >= _SERPER_FREE_LIMIT:
            logger.info(f"[web_searcher] Serper {_serper_call_count}回使用済み → Google CSEに切替")
        result = await _search_google_cse(query, gcs_key, gcs_cx, timeout)
        if result:
            return result

    # --- どちらも失敗 ---
    if not serper_key and not gcs_key:
        logger.warning("[web_searcher] SERPER_API_KEY も GOOGLE_CSE_API_KEY も未設定です")

    return None


async def batch_search_websites(
    companies: list[dict],
    concurrency: int = 3,
    delay: float = 2.0,
) -> list[dict]:
    """複数企業のHPを一括検索する。

    DuckDuckGoへの過負荷を避けるため concurrency=3 / delay=2.0秒 を推奨する。
    既に website_url が設定されている企業はスキップする（再開時の重複防止）。

    Args:
        companies:   [{"name": "...", "location": "...", "corporate_number": "..."}, ...]
                     "website_url" が空文字または未設定のもののみ検索する。
        concurrency: 同時検索数
        delay:       リクエスト間の待機秒数

    Returns:
        入力と同じリスト（website_url フィールドが追加/更新される）
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _search_one(company: dict) -> dict:
        # 既取得済みはスキップ
        if company.get("website_url"):
            return company

        async with semaphore:
            try:
                url = await search_company_website(
                    company_name=company.get("name", ""),
                    location=company.get("location", ""),
                )
                company["website_url"] = url or ""
            except Exception as e:
                logger.warning(f"[web_searcher] バッチエラー ({company.get('name', '')}): {e}")
                company["website_url"] = ""
            await asyncio.sleep(delay)

        return company

    tasks = [_search_one(c) for c in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # gather が例外を返した場合は元の dict をそのまま使う
    output: list[dict] = []
    for company, result in zip(companies, results):
        if isinstance(result, dict):
            output.append(result)
        else:
            company["website_url"] = company.get("website_url", "")
            output.append(company)

    found = sum(1 for c in output if c.get("website_url"))
    logger.info(f"[web_searcher] 完了: {len(output)}社 / HP発見: {found}社")
    return output

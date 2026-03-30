"""業界団体の会員名簿をスクレイピングしてリード情報を取得するマイクロエージェント。

対応団体:
  - 日本金型工業会 (jdmia) — https://www.jdmia.or.jp/srvchg/search/companylist.php
    約500社の会員企業（社名/住所/電話/FAX/代表者/HP/メール/型種）

制約:
  - robots.txt 確認済み: /srvchg/ は Disallow 対象外
  - レート制限: 1リクエスト/秒（CRAWL_DELAY_SEC で調整可能）
  - 取得データは呼び出し元でleadsテーブルに保存する（永続化は責務外）
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# 設定値
# -------------------------------------------------------------------------

JDMIA_BASE_URL = "https://www.jdmia.or.jp"
JDMIA_LIST_URL = f"{JDMIA_BASE_URL}/srvchg/search/companylist.php"
JDMIA_MEMBER_URL = f"{JDMIA_BASE_URL}/srvchg/search/member.php"

# クロール間隔（秒）。robots.txt には明示指定なし。1秒を設定。
CRAWL_DELAY_SEC: float = 1.0

# 同時接続数の上限（1サイトに対して 1 に制限）
MAX_CONCURRENT = 1

# HTTPタイムアウト
HTTP_TIMEOUT = 20.0

# User-Agent: 一般的なブラウザを模倣しつつ、ボット排除回避
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 型種キーワード → sub_industry マッピング
_KATA_SHURUI_MAP: dict[str, str] = {
    "プラ": "プラスチック金型",
    "プレス": "プレス金型",
    "ダイ": "ダイカスト金型",
    "鋳": "鋳造金型",
    "鍛": "鍛造金型",
    "ゴム": "ゴム金型",
    "ガラス": "ガラス金型",
    "他": "複合金型",
}


# -------------------------------------------------------------------------
# データモデル
# -------------------------------------------------------------------------


@dataclass
class DirectoryEntry:
    """業界団体名簿の1エントリ"""

    company_name: str
    address: str = ""
    phone: str = ""
    fax: str = ""
    representative: str = ""
    website_url: str = ""
    email: str = ""
    source: str = ""          # "jdmia" 等
    sub_industry: str = ""    # "プラスチック金型" / "プレス金型" 等
    raw_mold_types: str = ""  # 型種の生テキスト（後処理用）


# -------------------------------------------------------------------------
# 内部ユーティリティ
# -------------------------------------------------------------------------


def _build_client() -> httpx.AsyncClient:
    """共通ヘッダーを設定した httpx クライアントを生成する。"""
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
        },
        follow_redirects=True,
    )


def _extract_company_ids_from_list(html: str) -> list[tuple[str, str]]:
    """一覧ページのHTMLからC_IDとリンクテキスト（型種含む）を抽出する。

    Returns:
        [(c_id, link_text), ...]  c_id=0 のエントリは除外する
    """
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(r"member\.php\?C_ID=(\d+)")
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=pattern):
        href = a.get("href", "")
        m = pattern.search(href)
        if not m:
            continue
        c_id = m.group(1)
        if c_id == "0" or c_id in seen:
            continue
        seen.add(c_id)
        link_text = a.get_text(strip=True)
        results.append((c_id, link_text))

    return results


def _detect_sub_industry(link_text: str, mold_types_text: str) -> tuple[str, str]:
    """型種テキストからsub_industryを推定する。

    Args:
        link_text: 一覧ページのリンクテキスト（例: "ＩＳＫ(株)鋳ダイ"）
        mold_types_text: 詳細ページの型種テキスト（例: "鋳ダイ"）

    Returns:
        (sub_industry, raw_mold_types)
    """
    combined = link_text + " " + mold_types_text
    detected: list[str] = []
    for keyword, label in _KATA_SHURUI_MAP.items():
        if keyword in combined:
            detected.append(label)
    if detected:
        return detected[0], mold_types_text
    return "金型", mold_types_text


def _parse_member_detail(html: str, c_id: str) -> Optional[DirectoryEntry]:
    """詳細ページHTMLをパースしてDirectoryEntryを返す。

    HTML構造（2024年時点）:
        <div class="companyInfo" id="content">
          <h1 id="Pttl">社名</h1>
          <dl class="company-information">
            <dt>所在地/連絡先</dt>
            <dd>〒xxx...<br>住所<br>TEL：xxx<br>FAX：xxx</dd>
            <dt>代表者</dt>
            <dd>代表取締役 氏名</dd>
            <dt>ホームページ</dt>
            <dd><a href="http://...">...</a></dd>
            <dt>メールアドレス</dt>
            <dd><a href="mailto:...">...</a></dd>
            <dt>型種</dt>
            <dd>...</dd>
          </dl>
        </div>
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # 社名
        h1 = soup.find("h1", id="Pttl")
        if not h1:
            logger.debug(f"C_ID={c_id}: h1#Pttl not found")
            return None
        company_name = h1.get_text(strip=True)
        if not company_name:
            return None

        entry = DirectoryEntry(
            company_name=company_name,
            source="jdmia",
        )

        dl = soup.find("dl", class_="company-information")
        if not dl:
            logger.debug(f"C_ID={c_id}: dl.company-information not found")
            return entry

        dt_list = dl.find_all("dt")
        for dt in dt_list:
            label = dt.get_text(strip=True)
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            value_text = dd.get_text(" ", strip=True)

            if "所在地" in label or "連絡先" in label:
                # 〒xxxの郵便番号と住所・TEL・FAXを分離
                raw = dd.decode_contents()  # <br>をそのまま保持
                # <br> をニューラインに変換してから再取得
                for br in dd.find_all("br"):
                    br.replace_with("\n")
                lines = [l.strip() for l in dd.get_text().splitlines() if l.strip()]
                address_parts: list[str] = []
                for line in lines:
                    if line.startswith("TEL"):
                        m = re.search(r"TEL[：:]\s*([\d\-\(\)]+)", line)
                        if m:
                            entry.phone = m.group(1)
                    elif line.startswith("FAX"):
                        m = re.search(r"FAX[：:]\s*([\d\-\(\)]+)", line)
                        if m:
                            entry.fax = m.group(1)
                    else:
                        address_parts.append(line)
                entry.address = " ".join(address_parts)

            elif "代表者" in label:
                entry.representative = value_text

            elif "ホームページ" in label:
                a_tag = dd.find("a", href=True)
                if a_tag:
                    href = a_tag.get("href", "")
                    if href.startswith("http"):
                        entry.website_url = href

            elif "メールアドレス" in label:
                # mailto: リンクから取得
                a_tag = dd.find("a", href=re.compile(r"^mailto:"))
                if a_tag:
                    entry.email = a_tag.get("href", "").replace("mailto:", "").strip()
                else:
                    # テキストから正規表現で抽出（念のため）
                    m = re.search(
                        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
                        value_text,
                    )
                    if m:
                        entry.email = m.group(0)

            elif "型種" in label:
                entry.raw_mold_types = value_text

        return entry

    except Exception as exc:
        logger.warning(f"C_ID={c_id}: parse error — {exc}")
        return None


# -------------------------------------------------------------------------
# メインスクレイパー
# -------------------------------------------------------------------------


async def scrape_jdmia_members(
    max_companies: Optional[int] = None,
    crawl_delay: float = CRAWL_DELAY_SEC,
) -> list[DirectoryEntry]:
    """日本金型工業会の会員名簿をスクレイピングする。

    処理フロー:
        1. 一覧ページ (companylist.php) を取得し C_ID を全件収集
        2. 各詳細ページ (member.php?C_ID=xxx) を順次取得・パース
        3. 取得失敗のエントリはスキップしてログに記録

    Args:
        max_companies: 取得上限（None=全件。テスト用途で件数制限する場合に指定）
        crawl_delay: リクエスト間隔（秒）

    Returns:
        会員企業リスト。取得できなかったエントリは含まれない。
    """
    entries: list[DirectoryEntry] = []

    async with _build_client() as client:
        # --- Step 1: 一覧ページから C_ID を収集 ---
        logger.info(f"Fetching JDMIA list page: {JDMIA_LIST_URL}")
        try:
            resp = await client.get(JDMIA_LIST_URL)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(f"Failed to fetch JDMIA list page: {exc}")
            return []

        company_ids = _extract_company_ids_from_list(resp.text)
        logger.info(f"Found {len(company_ids)} companies on list page")

        if max_companies is not None:
            company_ids = company_ids[:max_companies]

        # --- Step 2: 各詳細ページを順次取得 ---
        success_count = 0
        skip_count = 0

        for idx, (c_id, link_text) in enumerate(company_ids):
            url = f"{JDMIA_MEMBER_URL}?C_ID={c_id}"

            try:
                await asyncio.sleep(crawl_delay)
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.warning(f"C_ID={c_id}: HTTP {exc.response.status_code} — skipped")
                skip_count += 1
                continue
            except httpx.HTTPError as exc:
                logger.warning(f"C_ID={c_id}: request error {exc} — skipped")
                skip_count += 1
                continue

            entry = _parse_member_detail(resp.text, c_id)
            if entry is None:
                logger.warning(f"C_ID={c_id}: parse returned None — skipped")
                skip_count += 1
                continue

            # リンクテキスト（型種含む）と詳細ページの型種テキストからsub_industry推定
            sub_industry, raw_mold_types = _detect_sub_industry(
                link_text, entry.raw_mold_types
            )
            entry.sub_industry = sub_industry
            entry.raw_mold_types = raw_mold_types

            entries.append(entry)
            success_count += 1

            if (idx + 1) % 50 == 0:
                logger.info(
                    f"JDMIA progress: {idx + 1}/{len(company_ids)} "
                    f"(success={success_count}, skip={skip_count})"
                )

    logger.info(
        f"JDMIA scrape complete: total={len(entries)}, skipped={skip_count}"
    )
    return entries


async def scrape_all_directories(
    max_per_source: Optional[int] = None,
) -> list[DirectoryEntry]:
    """全業界団体の名簿を一括スクレイピングする。

    現在の対応団体:
        - 日本金型工業会 (jdmia)

    Args:
        max_per_source: 各団体の取得上限（None=全件）

    Returns:
        全団体の会員企業リスト（重複除去なし）
    """
    results: list[DirectoryEntry] = []

    # --- 日本金型工業会 ---
    jdmia_entries = await scrape_jdmia_members(max_companies=max_per_source)
    results.extend(jdmia_entries)

    # 将来追加予定の団体は以下に続ける:
    # jpif_entries = await scrape_jpif_members(max_companies=max_per_source)
    # results.extend(jpif_entries)

    logger.info(f"scrape_all_directories: total {len(results)} entries collected")
    return results

"""企業HPからメールアドレス・お問い合わせフォームURLを自動抽出するマイクロエージェント。"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# メールアドレス抽出用正規表現
EMAIL_PATTERN = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
)

# お問い合わせページを示すキーワード（リンクテキスト・URL双方で照合）
CONTACT_PAGE_KEYWORDS = [
    'contact', 'inquiry', 'toiawase', 'otoiawase',
    'お問い合わせ', 'お問合せ', 'お問合わせ', 'ご相談', 'メールフォーム',
    'contact-us', 'contactus', 'form',
]

# 除外するメールアドレスパターン（誤検出排除）
_EXCLUDE_PATTERNS: list[re.Pattern] = [
    re.compile(r'.*\.(png|jpg|gif|svg|css|js)$', re.IGNORECASE),
    re.compile(r'^(info|admin|webmaster|noreply|no-reply)@example\.'),
    re.compile(r'.*@sentry\.'),
    re.compile(r'.*@wixpress\.'),
    re.compile(r'.*@w3\.org'),
    re.compile(r'.*@schemas\.'),
    re.compile(r'.*@openxmlformats\.'),
]

# リクエストヘッダ
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 電話番号抽出用正規表現（日本国内）
_PHONE_PATTERN = re.compile(
    r'(?:\+81[\s\-]?|0)(\d{1,4})[\s\-]?(\d{1,4})[\s\-]?(\d{3,4})'
)


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class ContactInfo:
    """企業の連絡先情報。"""
    company_name: str
    website_url: str
    emails: list[str] = field(default_factory=list)       # 優先度順メール一覧
    contact_form_url: str = ""                             # お問い合わせフォームURL
    phone: str = ""                                        # 電話番号（最初に見つかったもの）
    extraction_method: str = "none"                        # "email_direct" / "contact_form" / "none"
    error: Optional[str] = None                           # 取得失敗時のエラー概要


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _is_valid_email(email: str) -> bool:
    """除外パターンに該当しないメールアドレスかを確認する。"""
    for pat in _EXCLUDE_PATTERNS:
        if pat.match(email):
            return False
    # TLDが数字だけの場合は除外（IPアドレス誤検出防止）
    tld = email.rsplit('.', 1)[-1]
    if tld.isdigit():
        return False
    return True


def _extract_emails_from_html(html: str) -> list[str]:
    """HTML文字列からメールアドレスを抽出し、重複除去して返す。"""
    found = EMAIL_PATTERN.findall(html)
    seen: set[str] = set()
    result: list[str] = []
    for email in found:
        email = email.lower().rstrip('.')
        if email not in seen and _is_valid_email(email):
            seen.add(email)
            result.append(email)
    return result


def _extract_mailto_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """soup から mailto: リンクを抽出する。"""
    emails: list[str] = []
    seen: set[str] = set()
    for a_tag in soup.find_all('a', href=True):
        href: str = a_tag['href']
        if href.startswith('mailto:'):
            email = href[7:].split('?')[0].strip().lower()
            if email and email not in seen and _is_valid_email(email):
                seen.add(email)
                emails.append(email)
    return emails


def _find_contact_page_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """soup からお問い合わせページへのリンクURLを抽出する。"""
    candidates: list[str] = []
    seen: set[str] = set()
    base_domain = urlparse(base_url).netloc

    for a_tag in soup.find_all('a', href=True):
        href: str = a_tag['href'].strip()
        link_text: str = (a_tag.get_text() or '').strip()

        # 空・JavaScript・アンカーのみは除外
        if not href or href.startswith('javascript:') or href == '#':
            continue

        # キーワード判定（URL or リンクテキスト）
        href_lower = href.lower()
        text_lower = link_text.lower()
        matched = any(kw in href_lower or kw in text_lower for kw in CONTACT_PAGE_KEYWORDS)
        if not matched:
            continue

        abs_url = urljoin(base_url, href)
        # 同一ドメインのみ
        if urlparse(abs_url).netloc != base_domain:
            continue
        # フラグメントのみの相対URLは除外
        if abs_url == base_url:
            continue

        if abs_url not in seen:
            seen.add(abs_url)
            candidates.append(abs_url)

    return candidates


def _prioritize_emails(emails: list[str], domain: str) -> list[str]:
    """企業ドメインのメールを先頭に並べ替える。"""
    own: list[str] = []
    other: list[str] = []
    for email in emails:
        if email.endswith('@' + domain) or ('.' + domain) in email:
            own.append(email)
        else:
            other.append(email)
    return own + other


def _extract_phone(text: str) -> str:
    """テキストから最初の電話番号を抽出する。"""
    m = _PHONE_PATTERN.search(text)
    if m:
        return m.group(0)
    return ""


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------

async def extract_contact_from_website(
    company_name: str,
    website_url: str,
    timeout: float = 10.0,
) -> ContactInfo:
    """企業HPからメールアドレス・お問い合わせフォームURLを抽出する。

    処理フロー:
    1. トップページを取得
    2. mailto: リンクからメールを抽出
    3. 正規表現でHTMLからメールを抽出
    4. お問い合わせページへのリンクを探す
    5. お問い合わせページを取得してフォームURL・メールを追加抽出
    6. 企業ドメインのメールを優先度上位に並べ替え

    Args:
        company_name: 企業名（ログ・返戻値用）
        website_url:  企業ウェブサイトURL
        timeout:      HTTPリクエストのタイムアウト秒数

    Returns:
        ContactInfo: 抽出した連絡先情報
    """
    info = ContactInfo(company_name=company_name, website_url=website_url)

    if not website_url:
        info.error = "website_url が空です"
        return info

    # URL正規化
    if not website_url.startswith(('http://', 'https://')):
        website_url = 'https://' + website_url

    domain = urlparse(website_url).netloc.lstrip('www.')
    all_emails: list[str] = []
    contact_form_url = ""

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "ja, en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,  # SSL証明書エラーをスキップ（中小企業はHTTPSが不完全なことが多い）
            headers=headers,
        ) as client:
            # ---- Step 1: トップページ取得 ----
            try:
                resp = await client.get(website_url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                info.error = f"HTTP {e.response.status_code}: {website_url}"
                return info
            except Exception as e:
                info.error = f"トップページ取得失敗: {type(e).__name__}"
                return info

            top_html = resp.text
            top_soup = BeautifulSoup(top_html, 'html.parser')

            # ---- Step 2: mailto: リンクからメールを取得 ----
            mailto_emails = _extract_mailto_links(top_soup, website_url)
            all_emails.extend(mailto_emails)

            # ---- Step 3: 正規表現でHTMLからメールを抽出 ----
            regex_emails = _extract_emails_from_html(top_html)
            for email in regex_emails:
                if email not in all_emails:
                    all_emails.append(email)

            # ---- Step 4: 電話番号を抽出 ----
            info.phone = _extract_phone(top_soup.get_text())

            # ---- Step 5: お問い合わせページを探索 ----
            contact_links = _find_contact_page_links(top_soup, website_url)

            for contact_url in contact_links[:3]:  # 最大3ページまで探索
                try:
                    c_resp = await client.get(contact_url)
                    c_resp.raise_for_status()
                    c_html = c_resp.text
                    c_soup = BeautifulSoup(c_html, 'html.parser')

                    # フォームURLの候補として記録（最初に見つかったもの）
                    if not contact_form_url:
                        # form タグが存在するページをフォームURLとして記録
                        if c_soup.find('form'):
                            contact_form_url = contact_url
                        else:
                            contact_form_url = contact_url

                    # お問い合わせページからもメール抽出
                    c_mailto = _extract_mailto_links(c_soup, contact_url)
                    for email in c_mailto:
                        if email not in all_emails:
                            all_emails.append(email)

                    c_regex = _extract_emails_from_html(c_html)
                    for email in c_regex:
                        if email not in all_emails:
                            all_emails.append(email)

                except Exception:
                    # お問い合わせページの取得失敗はスキップ
                    continue

    except Exception as e:
        info.error = f"予期しないエラー: {type(e).__name__}: {e}"
        return info

    # ---- Step 6: 結果整理 ----
    info.emails = _prioritize_emails(all_emails, domain)
    info.contact_form_url = contact_form_url

    # 抽出方法の記録
    if info.emails:
        info.extraction_method = "email_direct"
    elif info.contact_form_url:
        info.extraction_method = "contact_form"
    else:
        info.extraction_method = "none"

    logger.debug(
        f"[contact_extractor] {company_name}: "
        f"emails={len(info.emails)}, form={bool(contact_form_url)}, "
        f"method={info.extraction_method}"
    )
    return info


async def extract_company_details(
    company_name: str,
    website_url: str,
    timeout: float = 10.0,
) -> dict:
    """企業HPから詳細情報を抽出する。

    トップページに加え、「会社概要」「会社案内」「about」ページも巡回して
    従業員数・事業内容・住所・FAX番号を取得する。

    Args:
        company_name: 企業名（ログ用）
        website_url:  企業ウェブサイトURL
        timeout:      HTTPタイムアウト（秒）

    Returns:
        {
            "emails": ["info@example.co.jp"],
            "phone": "03-1234-5678",
            "fax": "03-1234-5679",
            "employee_count": 150,
            "business_description": "精密金属加工・切削加工",
            "contact_form_url": "https://example.co.jp/contact/",
            "address": "東京都大田区...",
        }
    """
    result: dict = {
        "emails": [],
        "phone": "",
        "fax": "",
        "employee_count": None,
        "business_description": "",
        "contact_form_url": "",
        "address": "",
        "error": None,
    }

    if not website_url:
        result["error"] = "website_url が空です"
        return result

    if not website_url.startswith(("http://", "https://")):
        website_url = "https://" + website_url

    domain = urlparse(website_url).netloc.lstrip("www.")
    all_emails: list[str] = []
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "ja, en;q=0.9",
    }

    # 会社概要ページを示すキーワード
    company_profile_keywords = [
        "company", "about", "profile", "gaiyou", "annai",
        "会社概要", "会社案内", "企業情報", "企業概要", "about-us",
    ]

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            verify=False,
            headers=headers,
        ) as client:
            # ---- トップページ取得 ----
            try:
                resp = await client.get(website_url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                result["error"] = f"HTTP {e.response.status_code}: {website_url}"
                return result
            except Exception as e:
                result["error"] = f"トップページ取得失敗: {type(e).__name__}"
                return result

            top_html = resp.text
            top_soup = BeautifulSoup(top_html, "html.parser")
            top_text = top_soup.get_text(separator="\n")

            # メール抽出
            all_emails.extend(_extract_mailto_links(top_soup, website_url))
            for em in _extract_emails_from_html(top_html):
                if em not in all_emails:
                    all_emails.append(em)

            # 電話・FAX抽出
            result["phone"] = _extract_phone(top_text)
            result["fax"] = _extract_fax(top_text)

            # 従業員数・事業内容・住所（トップページから）
            if result["employee_count"] is None:
                result["employee_count"] = _extract_employee_count(top_text)
            if not result["business_description"]:
                result["business_description"] = _extract_business_description(top_soup)
            if not result["address"]:
                result["address"] = _extract_address(top_text)

            # お問い合わせページリンク
            contact_links = _find_contact_page_links(top_soup, website_url)
            if contact_links:
                result["contact_form_url"] = contact_links[0]

            # ---- お問い合わせページも巡回（電話・メール取得） ----
            for contact_url in contact_links[:1]:
                try:
                    c_resp = await client.get(contact_url)
                    c_resp.raise_for_status()
                    c_html = c_resp.text
                    c_soup = BeautifulSoup(c_html, "html.parser")
                    c_text = c_soup.get_text(separator="\n")

                    for em in _extract_mailto_links(c_soup, contact_url):
                        if em not in all_emails:
                            all_emails.append(em)
                    for em in _extract_emails_from_html(c_html):
                        if em not in all_emails:
                            all_emails.append(em)
                    if not result["phone"]:
                        result["phone"] = _extract_phone(c_text)
                    if not result["fax"]:
                        result["fax"] = _extract_fax(c_text)
                except Exception:
                    pass

            # ---- 会社概要ページを探索 ----
            profile_links = _find_profile_page_links(
                top_soup, website_url, company_profile_keywords
            )

            for profile_url in profile_links[:2]:  # 最大2ページ巡回
                try:
                    p_resp = await client.get(profile_url)
                    p_resp.raise_for_status()
                    p_html = p_resp.text
                    p_soup = BeautifulSoup(p_html, "html.parser")
                    p_text = p_soup.get_text(separator="\n")

                    # 会社概要ページからメール追加抽出
                    for em in _extract_mailto_links(p_soup, profile_url):
                        if em not in all_emails:
                            all_emails.append(em)
                    for em in _extract_emails_from_html(p_html):
                        if em not in all_emails:
                            all_emails.append(em)

                    # 電話・FAX（まだ取得できていなければ）
                    if not result["phone"]:
                        result["phone"] = _extract_phone(p_text)
                    if not result["fax"]:
                        result["fax"] = _extract_fax(p_text)

                    # 従業員数・事業内容・住所（会社概要ページを優先）
                    emp = _extract_employee_count(p_text)
                    if emp is not None:
                        result["employee_count"] = emp

                    desc = _extract_business_description(p_soup)
                    if desc:
                        result["business_description"] = desc

                    addr = _extract_address(p_text)
                    if addr:
                        result["address"] = addr

                except Exception:
                    continue

    except Exception as e:
        result["error"] = f"予期しないエラー: {type(e).__name__}: {e}"
        return result

    result["emails"] = _prioritize_emails(all_emails, domain)
    return result


# ---------------------------------------------------------------------------
# extract_company_details 用内部ユーティリティ
# ---------------------------------------------------------------------------

# FAX番号: "FAX" or "ファックス" の近くに続く電話番号パターン
_FAX_LABEL_RE = re.compile(
    r'(?:FAX|Fax|fax|ファックス|ファクス)[\s\t:：]*'
    r'((?:\+81[\s\-]?|0)\d{1,4}[\s\-]?\d{1,4}[\s\-]?\d{3,4})',
    re.IGNORECASE,
)

# 電話番号パターン（ラベル付きで TEL/FAX 判別に使う）
_TEL_LABEL_RE = re.compile(
    r'(?:TEL|Tel|tel|電話|お電話)[\s\t:：]*'
    r'((?:\+81[\s\-]?|0)\d{1,4}[\s\-]?\d{1,4}[\s\-]?\d{3,4})',
    re.IGNORECASE,
)

# 従業員数: 「従業員」「社員数」「人員」の後ろに続く数字
_EMPLOYEE_RE = re.compile(
    r'(?:従業員(?:数)?|社員数|人員|スタッフ数|従業者数)'
    r'[\s\t:：　]*'
    r'(?:約\s*)?'
    r'(\d[\d,，]*)'
    r'\s*(?:名|人|名様)?',
)

# 住所: 都道府県から始まる日本語住所
_ADDRESS_LABEL_RE = re.compile(
    r'(?:所在地|住所|本社|本店)[\s\t:：　]*'
    r'([^\n]{5,80})'
)

# 事業内容セクション見出し
_BUSINESS_SECTION_RE = re.compile(
    r'事業内容|業務内容|主要事業|取扱品目|取扱商品|製品・サービス'
)


def _extract_fax(text: str) -> str:
    """テキストからFAX番号を抽出する。"""
    m = _FAX_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    return ""


def _extract_employee_count(text: str) -> Optional[int]:
    """テキストから従業員数を抽出する。

    「従業員 150名」「社員数：300人」などのパターンに対応する。

    Returns:
        整数値（見つからなければ None）
    """
    m = _EMPLOYEE_RE.search(text)
    if m:
        raw = m.group(1).replace(",", "").replace("，", "")
        try:
            return int(raw)
        except ValueError:
            pass
    return None


def _extract_business_description(soup: BeautifulSoup) -> str:
    """soupから事業内容テキストを抽出する。

    「事業内容」「業務内容」の見出し直後のテキストを取得する。
    見つからなければ空文字を返す。

    Returns:
        事業内容テキスト（最大200文字）
    """
    # 見出しタグ (h1〜h4, dt, th, td, p) を探索
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "dt", "th", "td", "p", "strong"]):
        text = tag.get_text(strip=True)
        if _BUSINESS_SECTION_RE.search(text):
            # 直後の兄弟・子要素からテキストを取得
            next_tag = tag.find_next_sibling()
            if next_tag:
                desc = next_tag.get_text(separator=" ", strip=True)
                if desc:
                    return desc[:200]
            # 親の次の要素
            if tag.parent:
                parent_next = tag.parent.find_next_sibling()
                if parent_next:
                    desc = parent_next.get_text(separator=" ", strip=True)
                    if desc:
                        return desc[:200]
    return ""


def _extract_address(text: str) -> str:
    """テキストから住所を抽出する。

    「所在地」「住所」ラベルの後に続く文字列、または
    都道府県で始まる行を抽出する。

    Returns:
        住所文字列（最大100文字）
    """
    # ラベルベース抽出
    m = _ADDRESS_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()[:100]

    # 都道府県パターンから直接抽出
    pref_re = re.compile(
        r'(?:北海道|東京都|(?:大阪|京都)府|(?:神奈川|愛知|福岡|埼玉|千葉|兵庫|静岡|茨城|広島|新潟|宮城|長野|栃木|岐阜|群馬|岡山|福島|三重|熊本|鹿児島|山口|愛媛|長崎|滋賀|奈良|青森|岩手|大分|石川|宮崎|山形|富山|秋田|香川|和歌山|佐賀|福井|徳島|高知|島根|鳥取|沖縄)県)'
        r'[^\n]{3,80}'
    )
    m2 = pref_re.search(text)
    if m2:
        return m2.group(0).strip()[:100]

    return ""


def _find_profile_page_links(
    soup: BeautifulSoup,
    base_url: str,
    keywords: list[str],
) -> list[str]:
    """soupから会社概要ページへのリンクURLを抽出する。

    Args:
        soup:     トップページのBeautifulSoupオブジェクト
        base_url: ベースURL（同一ドメイン判定用）
        keywords: 見出し・URLに含まれるキーワードリスト

    Returns:
        会社概要ページURLのリスト（同一ドメインのみ）
    """
    candidates: list[str] = []
    seen: set[str] = set()
    base_domain = urlparse(base_url).netloc

    for a_tag in soup.find_all("a", href=True):
        href: str = a_tag["href"].strip()
        link_text: str = (a_tag.get_text() or "").strip()

        if not href or href.startswith("javascript:") or href == "#":
            continue

        href_lower = href.lower()
        text_lower = link_text.lower()
        matched = any(kw in href_lower or kw in text_lower for kw in keywords)
        if not matched:
            continue

        abs_url = urljoin(base_url, href)
        if urlparse(abs_url).netloc != base_domain:
            continue
        if abs_url == base_url:
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            candidates.append(abs_url)

    return candidates


async def batch_extract_contacts(
    companies: list[dict],
    concurrency: int = 5,
    delay: float = 1.0,
) -> list[ContactInfo]:
    """複数企業のHPから連絡先を一括抽出する。

    asyncio.Semaphore で同時接続数を制限し、
    各リクエスト間に delay 秒のスリープを挿入してサーバー負荷を抑制する。

    Args:
        companies:   [{"company_name": ..., "website_url": ...}, ...] のリスト
        concurrency: 同時並行リクエスト数（デフォルト: 5）
        delay:       リクエスト間の待機秒数（デフォルト: 1.0）

    Returns:
        ContactInfo のリスト（companies と同じ順序）
    """
    sem = asyncio.Semaphore(concurrency)
    results: list[ContactInfo] = [ContactInfo(company_name="", website_url="")] * len(companies)
    lock = asyncio.Lock()
    counter = 0

    async def _process(idx: int, company: dict) -> None:
        nonlocal counter
        async with sem:
            result = await extract_contact_from_website(
                company_name=company.get("company_name", ""),
                website_url=company.get("website_url", ""),
            )
            results[idx] = result
            async with lock:
                counter += 1
                if counter % 10 == 0:
                    logger.info(f"[batch_extract_contacts] 進捗: {counter}/{len(companies)}")
            await asyncio.sleep(delay)

    tasks = [_process(i, c) for i, c in enumerate(companies)]
    await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if not r.error)
    logger.info(
        f"[batch_extract_contacts] 完了: 合計={len(results)}, "
        f"成功={success}, 失敗={len(results) - success}"
    )
    return results

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
_USER_AGENT = "ShachoTwo-Bot/1.0 (営業リサーチ用)"

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

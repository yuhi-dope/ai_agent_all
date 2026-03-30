"""contact_extractor マイクロエージェントのユニットテスト。"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.contact_extractor import (
    ContactInfo,
    _extract_emails_from_html,
    _extract_mailto_links,
    _find_contact_page_links,
    _is_valid_email,
    _prioritize_emails,
    _extract_phone,
    extract_contact_from_website,
    batch_extract_contacts,
)
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# ユーティリティ関数テスト
# ---------------------------------------------------------------------------

class TestIsValidEmail:
    def test_valid_email(self):
        assert _is_valid_email("info@example-company.co.jp") is True

    def test_exclude_image_extension(self):
        assert _is_valid_email("logo@company.png") is False

    def test_exclude_sentry(self):
        assert _is_valid_email("error@sentry.io") is False

    def test_exclude_wixpress(self):
        assert _is_valid_email("user@wixpress.com") is False

    def test_exclude_numeric_tld(self):
        assert _is_valid_email("user@192.168.1") is False

    def test_valid_corporate_email(self):
        assert _is_valid_email("contact@tanaka-seisakusho.co.jp") is True


class TestExtractEmailsFromHtml:
    def test_finds_plain_email(self):
        html = "<p>お問い合わせ: info@tanaka-mfg.co.jp まで</p>"
        result = _extract_emails_from_html(html)
        assert "info@tanaka-mfg.co.jp" in result

    def test_deduplicates(self):
        html = "<p>info@company.jp info@company.jp</p>"
        result = _extract_emails_from_html(html)
        assert result.count("info@company.jp") == 1

    def test_excludes_invalid(self):
        html = "<p>logo@site.png test@sentry.io</p>"
        result = _extract_emails_from_html(html)
        assert len(result) == 0

    def test_multiple_emails(self):
        html = "<p>sales@abc.co.jp support@abc.co.jp</p>"
        result = _extract_emails_from_html(html)
        assert len(result) == 2


class TestExtractMailtoLinks:
    def test_finds_mailto(self):
        html = '<a href="mailto:info@tanaka.co.jp">メール</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_mailto_links(soup, "https://tanaka.co.jp")
        assert "info@tanaka.co.jp" in result

    def test_strips_query_params(self):
        html = '<a href="mailto:info@tanaka.co.jp?subject=お問い合わせ">メール</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_mailto_links(soup, "https://tanaka.co.jp")
        assert "info@tanaka.co.jp" in result
        assert "?" not in result[0]

    def test_no_mailto(self):
        html = '<a href="https://tanaka.co.jp/contact">問い合わせ</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_mailto_links(soup, "https://tanaka.co.jp")
        assert result == []


class TestFindContactPageLinks:
    def test_finds_contact_link_by_text(self):
        html = '<a href="/contact">お問い合わせ</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _find_contact_page_links(soup, "https://tanaka.co.jp")
        assert "https://tanaka.co.jp/contact" in result

    def test_finds_contact_link_by_url(self):
        html = '<a href="/inquiry/form">詳細はこちら</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _find_contact_page_links(soup, "https://tanaka.co.jp")
        assert "https://tanaka.co.jp/inquiry/form" in result

    def test_excludes_external_domain(self):
        html = '<a href="https://other.co.jp/contact">外部リンク</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _find_contact_page_links(soup, "https://tanaka.co.jp")
        assert result == []

    def test_excludes_javascript(self):
        html = '<a href="javascript:void(0)">お問い合わせ</a>'
        soup = BeautifulSoup(html, 'html.parser')
        result = _find_contact_page_links(soup, "https://tanaka.co.jp")
        assert result == []


class TestPrioritizeEmails:
    def test_own_domain_first(self):
        emails = ["user@gmail.com", "info@tanaka.co.jp", "other@yahoo.co.jp"]
        result = _prioritize_emails(emails, "tanaka.co.jp")
        assert result[0] == "info@tanaka.co.jp"

    def test_no_own_domain(self):
        emails = ["user@gmail.com", "other@yahoo.co.jp"]
        result = _prioritize_emails(emails, "tanaka.co.jp")
        assert result == emails


class TestExtractPhone:
    def test_finds_phone_number(self):
        text = "TEL: 052-123-4567"
        result = _extract_phone(text)
        assert "052" in result

    def test_no_phone(self):
        result = _extract_phone("お問い合わせはメールにてお願いします。")
        assert result == ""


# ---------------------------------------------------------------------------
# extract_contact_from_website テスト（httpx モック）
# ---------------------------------------------------------------------------

TOP_PAGE_HTML = """
<html>
<body>
  <a href="mailto:info@tanaka-mfg.co.jp">メールはこちら</a>
  <a href="/contact">お問い合わせ</a>
  <p>TEL: 052-111-2222</p>
</body>
</html>
"""

CONTACT_PAGE_HTML = """
<html>
<body>
  <form action="/send">
    <input type="text" name="name">
    <input type="submit">
  </form>
  <p>info@tanaka-mfg.co.jp</p>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_extract_contact_success():
    """正常系: トップページ + お問い合わせページからメール・フォームURLを抽出できる。"""
    mock_response_top = MagicMock()
    mock_response_top.status_code = 200
    mock_response_top.text = TOP_PAGE_HTML
    mock_response_top.raise_for_status = MagicMock()

    mock_response_contact = MagicMock()
    mock_response_contact.status_code = 200
    mock_response_contact.text = CONTACT_PAGE_HTML
    mock_response_contact.raise_for_status = MagicMock()

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_response_top
        return mock_response_contact

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        result = await extract_contact_from_website(
            company_name="田中製作所",
            website_url="https://tanaka-mfg.co.jp",
        )

    assert result.error is None
    assert "info@tanaka-mfg.co.jp" in result.emails
    assert result.contact_form_url == "https://tanaka-mfg.co.jp/contact"
    assert result.extraction_method == "email_direct"
    assert "052" in result.phone


@pytest.mark.asyncio
async def test_extract_contact_empty_url():
    """異常系: website_url が空の場合はエラーを返す。"""
    result = await extract_contact_from_website(
        company_name="テスト会社",
        website_url="",
    )
    assert result.error is not None
    assert result.emails == []


@pytest.mark.asyncio
async def test_extract_contact_http_error():
    """異常系: HTTPエラー時はエラー情報を格納して返す。"""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 404

    async def mock_get(url, **kwargs):
        raise httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_response,
        )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        result = await extract_contact_from_website(
            company_name="存在しない会社",
            website_url="https://nonexistent-company.co.jp",
        )

    assert result.error is not None
    assert "404" in result.error


# ---------------------------------------------------------------------------
# batch_extract_contacts テスト
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_extract_contacts():
    """batch_extract_contacts が全企業を処理し結果を返す。"""
    companies = [
        {"company_name": "田中製作所", "website_url": "https://tanaka.co.jp"},
        {"company_name": "鈴木金属", "website_url": "https://suzuki-metal.co.jp"},
        {"company_name": "URLなし会社", "website_url": ""},
    ]

    async def mock_extract(company_name, website_url, **kwargs):
        if not website_url:
            return ContactInfo(company_name=company_name, website_url=website_url, error="website_url が空です")
        return ContactInfo(
            company_name=company_name,
            website_url=website_url,
            emails=[f"info@{urlparse(website_url).netloc}"],
            extraction_method="email_direct",
        )

    from urllib.parse import urlparse

    with patch(
        "workers.micro.contact_extractor.extract_contact_from_website",
        side_effect=mock_extract,
    ):
        results = await batch_extract_contacts(companies, concurrency=2, delay=0.0)

    assert len(results) == 3
    assert results[0].company_name == "田中製作所"
    assert results[1].company_name == "鈴木金属"
    assert results[2].error is not None  # URLなし


@pytest.mark.asyncio
async def test_batch_extract_contacts_empty():
    """batch_extract_contacts に空リストを渡しても正常に返る。"""
    results = await batch_extract_contacts([], concurrency=3, delay=0.0)
    assert results == []

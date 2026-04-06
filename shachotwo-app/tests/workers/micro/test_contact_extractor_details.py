"""contact_extractor の extract_company_details 拡張機能のユニットテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.contact_extractor import (
    _extract_fax,
    _extract_employee_count,
    _extract_business_description,
    _extract_address,
    _find_profile_page_links,
    extract_company_details,
)
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# _extract_fax
# ---------------------------------------------------------------------------

class TestExtractFax:
    def test_finds_fax_label(self):
        text = "TEL: 052-111-2222\nFAX: 052-111-2223"
        assert "052-111-2223" in _extract_fax(text)

    def test_finds_japanese_label(self):
        text = "ファックス：03-1234-5679"
        assert "03-1234-5679" in _extract_fax(text)

    def test_no_fax(self):
        assert _extract_fax("TEL: 052-111-2222") == ""

    def test_fax_with_spaces(self):
        text = "FAX  052 111 2224"
        result = _extract_fax(text)
        assert result != ""


# ---------------------------------------------------------------------------
# _extract_employee_count
# ---------------------------------------------------------------------------

class TestExtractEmployeeCount:
    def test_finds_employee_count(self):
        text = "従業員数: 150名"
        assert _extract_employee_count(text) == 150

    def test_finds_with_comma(self):
        text = "社員数：1,200名"
        assert _extract_employee_count(text) == 1200

    def test_finds_with_yaku(self):
        text = "従業員 約300人"
        assert _extract_employee_count(text) == 300

    def test_finds_staff_count(self):
        text = "スタッフ数：50名"
        assert _extract_employee_count(text) == 50

    def test_no_count(self):
        assert _extract_employee_count("お問い合わせはこちら") is None

    def test_finds_jugyosha(self):
        text = "従業者数　200名"
        assert _extract_employee_count(text) == 200


# ---------------------------------------------------------------------------
# _extract_business_description
# ---------------------------------------------------------------------------

BUSINESS_SECTION_HTML = """
<html>
<body>
  <h3>事業内容</h3>
  <p>精密金属加工・切削加工・板金加工を主な事業としています。</p>
</body>
</html>
"""

BUSINESS_SECTION_HTML_DT = """
<html>
<body>
  <dl>
    <dt>業務内容</dt>
    <dd>産業機械部品の設計・製造・販売</dd>
  </dl>
</body>
</html>
"""


class TestExtractBusinessDescription:
    def test_finds_after_h3(self):
        soup = BeautifulSoup(BUSINESS_SECTION_HTML, "html.parser")
        result = _extract_business_description(soup)
        assert "精密金属加工" in result

    def test_finds_after_dt(self):
        soup = BeautifulSoup(BUSINESS_SECTION_HTML_DT, "html.parser")
        result = _extract_business_description(soup)
        assert "産業機械" in result

    def test_no_section(self):
        soup = BeautifulSoup("<html><body><p>会社概要なし</p></body></html>", "html.parser")
        result = _extract_business_description(soup)
        assert result == ""

    def test_truncates_at_200(self):
        long_text = "A" * 300
        html = f"<h3>事業内容</h3><p>{long_text}</p>"
        soup = BeautifulSoup(html, "html.parser")
        result = _extract_business_description(soup)
        assert len(result) <= 200


# ---------------------------------------------------------------------------
# _extract_address
# ---------------------------------------------------------------------------

class TestExtractAddress:
    def test_finds_label_based(self):
        text = "所在地: 東京都大田区西蒲田1-2-3"
        result = _extract_address(text)
        assert "東京都" in result
        assert "大田区" in result

    def test_finds_pref_pattern(self):
        text = "本社は愛知県名古屋市中区錦1-2-3にあります。"
        result = _extract_address(text)
        assert "愛知県" in result

    def test_finds_osaka(self):
        text = "住所：大阪府大阪市北区梅田1-1-1"
        result = _extract_address(text)
        assert "大阪府" in result

    def test_no_address(self):
        result = _extract_address("お問い合わせはメールにてお願いします。")
        assert result == ""

    def test_truncates_at_100(self):
        text = "所在地: 東京都大田区" + "あ" * 200
        result = _extract_address(text)
        assert len(result) <= 100


# ---------------------------------------------------------------------------
# _find_profile_page_links
# ---------------------------------------------------------------------------

class TestFindProfilePageLinks:
    def test_finds_company_profile_link(self):
        html = '<a href="/company">会社概要</a>'
        soup = BeautifulSoup(html, "html.parser")
        keywords = ["会社概要", "about", "company"]
        result = _find_profile_page_links(soup, "https://tanaka.co.jp", keywords)
        assert "https://tanaka.co.jp/company" in result

    def test_finds_about_link(self):
        html = '<a href="/about-us">About Us</a>'
        soup = BeautifulSoup(html, "html.parser")
        keywords = ["about"]
        result = _find_profile_page_links(soup, "https://tanaka.co.jp", keywords)
        assert "https://tanaka.co.jp/about-us" in result

    def test_excludes_external_domain(self):
        html = '<a href="https://other.co.jp/company">外部会社概要</a>'
        soup = BeautifulSoup(html, "html.parser")
        keywords = ["company"]
        result = _find_profile_page_links(soup, "https://tanaka.co.jp", keywords)
        assert result == []

    def test_excludes_javascript(self):
        html = '<a href="javascript:void(0)">会社概要</a>'
        soup = BeautifulSoup(html, "html.parser")
        keywords = ["会社概要"]
        result = _find_profile_page_links(soup, "https://tanaka.co.jp", keywords)
        assert result == []

    def test_deduplicates(self):
        html = '<a href="/company">会社概要</a><a href="/company">会社情報</a>'
        soup = BeautifulSoup(html, "html.parser")
        keywords = ["company"]
        result = _find_profile_page_links(soup, "https://tanaka.co.jp", keywords)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# extract_company_details（httpx モック）
# ---------------------------------------------------------------------------

TOP_PAGE_WITH_DETAILS = """
<html>
<body>
  <a href="mailto:info@tanaka-mfg.co.jp">メール</a>
  <a href="/company">会社概要</a>
  <a href="/contact">お問い合わせ</a>
  <p>TEL: 052-111-2222</p>
  <p>FAX: 052-111-2223</p>
  <p>従業員数: 85名</p>
</body>
</html>
"""

COMPANY_PAGE_WITH_DETAILS = """
<html>
<body>
  <h3>事業内容</h3>
  <p>精密金属加工・切削加工・板金加工</p>
  <dl>
    <dt>所在地</dt>
    <dd>愛知県名古屋市中区錦1-2-3</dd>
    <dt>従業員数</dt>
    <dd>85名</dd>
  </dl>
  <p>sales@tanaka-mfg.co.jp</p>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_extract_company_details_full():
    """正常系: トップページ + 会社概要ページから全フィールドを抽出できる。"""
    call_count = 0

    mock_top = MagicMock()
    mock_top.status_code = 200
    mock_top.text = TOP_PAGE_WITH_DETAILS
    mock_top.raise_for_status = MagicMock()

    mock_company = MagicMock()
    mock_company.status_code = 200
    mock_company.text = COMPANY_PAGE_WITH_DETAILS
    mock_company.raise_for_status = MagicMock()

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_top
        return mock_company

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = mock_get
        mock_cls.return_value = mock_client

        result = await extract_company_details(
            company_name="田中製作所",
            website_url="https://tanaka-mfg.co.jp",
        )

    assert result["error"] is None
    assert "info@tanaka-mfg.co.jp" in result["emails"]
    assert "052" in result["phone"]
    assert "052" in result["fax"]
    assert result["employee_count"] == 85
    assert "精密金属加工" in result["business_description"]
    assert "愛知県" in result["address"]
    assert result["contact_form_url"] != ""


@pytest.mark.asyncio
async def test_extract_company_details_empty_url():
    """異常系: website_url が空の場合はエラーを返す。"""
    result = await extract_company_details(
        company_name="テスト会社",
        website_url="",
    )
    assert result["error"] is not None
    assert result["emails"] == []


@pytest.mark.asyncio
async def test_extract_company_details_http_error():
    """異常系: HTTPエラー時はエラーフィールドに情報を格納する。"""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 404

    async def mock_get(url, **kwargs):
        raise httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_response,
        )

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = mock_get
        mock_cls.return_value = mock_client

        result = await extract_company_details(
            company_name="存在しない会社",
            website_url="https://nonexistent.co.jp",
        )

    assert result["error"] is not None
    assert "404" in result["error"]
    assert result["emails"] == []


@pytest.mark.asyncio
async def test_extract_company_details_no_subpage():
    """会社概要ページが存在しない場合でもトップページの情報を返す。"""
    top_html = """
    <html><body>
      <a href="mailto:info@simple.co.jp">メール</a>
      <p>TEL: 03-9999-8888</p>
    </body></html>
    """
    mock_top = MagicMock()
    mock_top.status_code = 200
    mock_top.text = top_html
    mock_top.raise_for_status = MagicMock()

    async def mock_get(url, **kwargs):
        return mock_top

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = mock_get
        mock_cls.return_value = mock_client

        result = await extract_company_details(
            company_name="シンプル株式会社",
            website_url="https://simple.co.jp",
        )

    assert result["error"] is None
    assert "info@simple.co.jp" in result["emails"]
    assert "03" in result["phone"]

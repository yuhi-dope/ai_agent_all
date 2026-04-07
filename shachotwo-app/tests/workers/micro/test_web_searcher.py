"""web_searcher マイクロエージェントのユニットテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.web_searcher import (
    _extract_actual_url,
    _is_likely_company_website,
    search_company_website,
    batch_search_websites,
)


# ---------------------------------------------------------------------------
# _extract_actual_url
# ---------------------------------------------------------------------------

class TestExtractActualUrl:
    def test_uddg_param(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ftanaka-mfg.co.jp&rut=abc"
        assert _extract_actual_url(href) == "https://tanaka-mfg.co.jp"

    def test_direct_https(self):
        assert _extract_actual_url("https://example.co.jp") == "https://example.co.jp"

    def test_protocol_relative(self):
        result = _extract_actual_url("//example.co.jp/path")
        assert result == "https://example.co.jp/path"

    def test_empty_string(self):
        assert _extract_actual_url("") is None

    def test_no_match(self):
        assert _extract_actual_url("javascript:void(0)") is None

    def test_uddg_with_special_chars(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.suzuki-kinzoku.co.jp%2Fabout"
        result = _extract_actual_url(href)
        assert result == "https://www.suzuki-kinzoku.co.jp/about"


# ---------------------------------------------------------------------------
# _is_likely_company_website
# ---------------------------------------------------------------------------

class TestIsLikelyCompanyWebsite:
    def test_valid_company_url(self):
        assert _is_likely_company_website("https://tanaka-mfg.co.jp") is True

    def test_exclude_facebook(self):
        assert _is_likely_company_website("https://www.facebook.com/tanaka") is False

    def test_exclude_twitter(self):
        assert _is_likely_company_website("https://twitter.com/tanaka") is False

    def test_exclude_wikipedia(self):
        assert _is_likely_company_website("https://ja.wikipedia.org/wiki/製造業") is False

    def test_exclude_indeed(self):
        assert _is_likely_company_website("https://jp.indeed.com/jobs") is False

    def test_exclude_go_jp(self):
        assert _is_likely_company_website("https://www.meti.go.jp") is False

    def test_exclude_note(self):
        assert _is_likely_company_website("https://note.com/tanaka") is False

    def test_exclude_amazon(self):
        assert _is_likely_company_website("https://amazon.co.jp/product") is False

    def test_valid_with_www(self):
        assert _is_likely_company_website("https://www.ohta-kinzoku.co.jp") is True

    def test_invalid_url_no_netloc(self):
        assert _is_likely_company_website("not-a-url") is False

    def test_empty_string(self):
        assert _is_likely_company_website("") is False


# ---------------------------------------------------------------------------
# search_company_website（httpx モック）
# ---------------------------------------------------------------------------

DUCKDUCKGO_HTML = """
<html>
<body>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftanaka-mfg.co.jp">
      田中製作所 公式サイト
    </a>
  </div>
</body>
</html>
"""

DUCKDUCKGO_HTML_EXCLUDED = """
<html>
<body>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.facebook.com%2Ftanaka">
      田中製作所 Facebook
    </a>
  </div>
</body>
</html>
"""

DUCKDUCKGO_HTML_NO_RESULTS = """
<html>
<body>
  <p>検索結果が見つかりませんでした。</p>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_search_company_website_success():
    """正常系: Serper APIで企業HPのURLを取得できる。"""
    import os

    async def mock_search_serper(query, api_key, timeout):
        # 実装では [{"link": "https://tanaka-mfg.co.jp"}] のようなレスポンスを返す
        return "https://tanaka-mfg.co.jp"

    with patch.dict(os.environ, {"SERPER_API_KEY": "test-key"}), \
         patch("workers.micro.web_searcher._search_serper", side_effect=mock_search_serper):
        result = await search_company_website("田中製作所", location="愛知県")

    assert result == "https://tanaka-mfg.co.jp"


@pytest.mark.asyncio
async def test_search_company_website_all_excluded():
    """除外ドメインのみの結果の場合は None を返す。"""
    async def mock_search_serper(query, api_key, timeout):
        # 除外ドメインのみを返す
        return None

    async def mock_search_google_cse(query, api_key, cx, timeout):
        # Google CSE も失敗
        return None

    with patch("workers.micro.web_searcher._search_serper", side_effect=mock_search_serper), \
         patch("workers.micro.web_searcher._search_google_cse", side_effect=mock_search_google_cse):
        result = await search_company_website("田中製作所")

    assert result is None


@pytest.mark.asyncio
async def test_search_company_website_no_results():
    """検索結果なしの場合は None を返す。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = DUCKDUCKGO_HTML_NO_RESULTS

    async def mock_post(url, **kwargs):
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_cls.return_value = mock_client

        result = await search_company_website("存在しない会社XYZ")

    assert result is None


@pytest.mark.asyncio
async def test_search_company_website_http_error():
    """DuckDuckGoがHTTPエラーを返した場合は None を返す（例外は上位に漏らさない）。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = ""

    async def mock_post(url, **kwargs):
        return mock_resp

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_cls.return_value = mock_client

        result = await search_company_website("田中製作所")

    assert result is None


@pytest.mark.asyncio
async def test_search_company_website_network_error():
    """ネットワークエラー時は None を返す（例外は上位に漏らさない）。"""
    import httpx

    async def mock_post(url, **kwargs):
        raise httpx.ConnectError("接続失敗")

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_cls.return_value = mock_client

        result = await search_company_website("田中製作所")

    assert result is None


# ---------------------------------------------------------------------------
# batch_search_websites
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_search_websites_basic():
    """基本動作: 複数企業を検索して website_url が付与される。"""
    companies = [
        {"name": "田中製作所", "location": "愛知県", "corporate_number": "1234567890123"},
        {"name": "鈴木金属工業", "location": "東京都", "corporate_number": "9876543210987"},
    ]

    async def mock_search(company_name, location="", timeout=10.0):
        if "田中" in company_name:
            return "https://tanaka-mfg.co.jp"
        if "鈴木" in company_name:
            return "https://suzuki-kinzoku.co.jp"
        return None

    with patch(
        "workers.micro.web_searcher.search_company_website",
        side_effect=mock_search,
    ):
        results = await batch_search_websites(companies, concurrency=2, delay=0.0)

    assert len(results) == 2
    assert results[0]["website_url"] == "https://tanaka-mfg.co.jp"
    assert results[1]["website_url"] == "https://suzuki-kinzoku.co.jp"


@pytest.mark.asyncio
async def test_batch_search_websites_skip_existing():
    """website_url が既に設定されている企業はスキップする。"""
    companies = [
        {
            "name": "既取得済み株式会社",
            "location": "大阪府",
            "corporate_number": "1111111111111",
            "website_url": "https://already-found.co.jp",
        },
        {
            "name": "未取得株式会社",
            "location": "福岡県",
            "corporate_number": "2222222222222",
        },
    ]

    call_log: list[str] = []

    async def mock_search(company_name, location="", timeout=10.0):
        call_log.append(company_name)
        return "https://mitori.co.jp"

    with patch(
        "workers.micro.web_searcher.search_company_website",
        side_effect=mock_search,
    ):
        results = await batch_search_websites(companies, concurrency=2, delay=0.0)

    # 既取得済みは search_company_website を呼ばない
    assert "既取得済み株式会社" not in call_log
    assert "未取得株式会社" in call_log
    # 既取得済みの website_url は保持される
    assert results[0]["website_url"] == "https://already-found.co.jp"


@pytest.mark.asyncio
async def test_batch_search_websites_empty():
    """空リストを渡しても正常に返る。"""
    results = await batch_search_websites([], concurrency=3, delay=0.0)
    assert results == []


@pytest.mark.asyncio
async def test_batch_search_websites_partial_failure():
    """一部の検索が失敗してもリスト全体が返る。"""
    companies = [
        {"name": "成功会社", "location": "東京都", "corporate_number": "1111111111111"},
        {"name": "失敗会社", "location": "大阪府", "corporate_number": "2222222222222"},
    ]

    async def mock_search(company_name, location="", timeout=10.0):
        if "成功" in company_name:
            return "https://success.co.jp"
        raise RuntimeError("検索エラー")

    with patch(
        "workers.micro.web_searcher.search_company_website",
        side_effect=mock_search,
    ):
        results = await batch_search_websites(companies, concurrency=2, delay=0.0)

    assert len(results) == 2
    success = next(r for r in results if r["corporate_number"] == "1111111111111")
    failure = next(r for r in results if r["corporate_number"] == "2222222222222")
    assert success["website_url"] == "https://success.co.jp"
    assert failure["website_url"] == ""

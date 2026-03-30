"""tests/workers/micro/test_industry_directory_scraper.py

industry_directory_scraper のユニットテスト。
外部HTTPリクエストは全て httpx.AsyncClient をモックして実施。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.micro.industry_directory_scraper import (
    DirectoryEntry,
    _detect_sub_industry,
    _extract_company_ids_from_list,
    _parse_member_detail,
    scrape_jdmia_members,
)

# ---------------------------------------------------------------------------
# テスト用HTMLフィクスチャ
# ---------------------------------------------------------------------------

LIST_HTML = """
<html><body>
<a href="member.php?C_ID=560">ＩＳＫ(株)鋳ダイ</a>
<a href="member.php?C_ID=415">(株)ISデザインプラ他</a>
<a href="member.php?C_ID=0">(株)青山製作所プラ</a>
<a href="member.php?C_ID=339">(株)アイエムデー工業プレス</a>
<a href="member.php?C_ID=560">ＩＳＫ(株)鋳ダイ</a>
</body></html>
"""

MEMBER_HTML_FULL = """
<html><body>
<div class="companyInfo" id="content">
  <h1 id="Pttl">ＩＳＫ株式会社</h1>
  <p class="membership"><span class="sei">正会員</span></p>
  <dl class="company-information">
    <dt class="headline">企業情報</dt>
    <dt>所在地/連絡先</dt>
    <dd>〒438-0045<br/>静岡県磐田市上岡田480-1<br/>TEL：0538-36-1151<br/>FAX：0538-35-6681</dd>
    <dt>代表者</dt>
    <dd>代表取締役　鈴木　雅博</dd>
    <dt>ホームページ</dt>
    <dd><a href="http://www.isk-jp.com" target="_blank">http://www.isk-jp.com</a></dd>
    <dt>メールアドレス</dt>
    <dd><a href="mailto:isk@isk-jp.com" target="_blank">isk@isk-jp.com</a></dd>
    <dt>型種</dt>
    <dd><span class="chi">鋳</span><span class="die">ダイ</span></dd>
  </dl>
</div>
</body></html>
"""

MEMBER_HTML_NO_EMAIL = """
<html><body>
<div class="companyInfo" id="content">
  <h1 id="Pttl">テスト金型工業株式会社</h1>
  <dl class="company-information">
    <dt>所在地/連絡先</dt>
    <dd>〒100-0001<br/>東京都千代田区大手町1-1<br/>TEL：03-1234-5678<br/>FAX：03-1234-5679</dd>
    <dt>代表者</dt>
    <dd>代表取締役　山田　太郎</dd>
    <dt>ホームページ</dt>
    <dd><a href="https://example.co.jp" target="_blank">https://example.co.jp</a></dd>
    <dt>型種</dt>
    <dd><span>プラ</span></dd>
  </dl>
</div>
</body></html>
"""

MEMBER_HTML_NO_H1 = """
<html><body>
<div class="companyInfo" id="content">
  <dl class="company-information">
    <dt>所在地/連絡先</dt>
    <dd>〒000-0000<br/>不明<br/>TEL：000-000-0000</dd>
  </dl>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# ユニットテスト: _extract_company_ids_from_list
# ---------------------------------------------------------------------------


def test_extract_company_ids_basic():
    """一覧HTMLからC_IDとリンクテキストを正しく抽出する。"""
    result = _extract_company_ids_from_list(LIST_HTML)
    ids = [r[0] for r in result]
    # C_ID=0 は除外される
    assert "0" not in ids
    # C_ID=560 は重複排除されて1件のみ
    assert ids.count("560") == 1
    # 3件: 560, 415, 339
    assert len(result) == 3


def test_extract_company_ids_link_text():
    """リンクテキスト（型種含む）が正しく取得される。"""
    result = _extract_company_ids_from_list(LIST_HTML)
    id_map = {c_id: text for c_id, text in result}
    assert "鋳ダイ" in id_map["560"]
    assert "プラ" in id_map["415"]


def test_extract_company_ids_empty_html():
    """会員リンクがないHTMLは空リストを返す。"""
    result = _extract_company_ids_from_list("<html><body>no links</body></html>")
    assert result == []


# ---------------------------------------------------------------------------
# ユニットテスト: _parse_member_detail
# ---------------------------------------------------------------------------


def test_parse_member_detail_full():
    """全フィールドが揃ったHTMLを正しくパースする。"""
    entry = _parse_member_detail(MEMBER_HTML_FULL, "560")
    assert entry is not None
    assert entry.company_name == "ＩＳＫ株式会社"
    assert "磐田" in entry.address
    assert entry.phone == "0538-36-1151"
    assert entry.fax == "0538-35-6681"
    # 代表者テキストに全角スペース(U+3000)が含まれる場合も許容
    assert "鈴木" in entry.representative
    assert "雅博" in entry.representative
    assert entry.website_url == "http://www.isk-jp.com"
    assert entry.email == "isk@isk-jp.com"
    assert entry.source == "jdmia"


def test_parse_member_detail_no_email():
    """メールアドレスがない場合は email が空文字になる。"""
    entry = _parse_member_detail(MEMBER_HTML_NO_EMAIL, "999")
    assert entry is not None
    assert entry.company_name == "テスト金型工業株式会社"
    assert entry.email == ""
    assert entry.phone == "03-1234-5678"
    assert entry.website_url == "https://example.co.jp"


def test_parse_member_detail_no_h1_returns_none():
    """h1#Pttl がないHTMLは None を返す。"""
    entry = _parse_member_detail(MEMBER_HTML_NO_H1, "999")
    assert entry is None


def test_parse_member_detail_broken_html():
    """壊れたHTMLは None を返す（例外を上位に伝播させない）。"""
    entry = _parse_member_detail("", "0")
    assert entry is None


# ---------------------------------------------------------------------------
# ユニットテスト: _detect_sub_industry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("link_text,mold_text,expected_prefix", [
    ("(株)テストプラ他", "プラ", "プラスチック金型"),
    ("(株)テストプレス", "プレス", "プレス金型"),
    ("ＩＳＫ(株)鋳ダイ", "鋳ダイ", "ダイカスト金型"),  # "ダイ"がマッチ
    ("(株)テスト工業他", "他", "複合金型"),
    ("(株)テスト工業", "", "金型"),  # マッチなし → デフォルト
])
def test_detect_sub_industry(link_text, mold_text, expected_prefix):
    sub_industry, _ = _detect_sub_industry(link_text, mold_text)
    assert sub_industry == expected_prefix or sub_industry.startswith(expected_prefix.split("金型")[0])


# ---------------------------------------------------------------------------
# 統合テスト: scrape_jdmia_members (httpx をモック)
# ---------------------------------------------------------------------------


def _make_mock_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_scrape_jdmia_members_success():
    """正常系: 一覧2件 → 詳細2件取得。"""
    list_html = """
    <html><body>
    <a href="member.php?C_ID=560">ＩＳＫ(株)鋳ダイ</a>
    <a href="member.php?C_ID=415">(株)ISデザインプラ他</a>
    </body></html>
    """
    detail_html_1 = MEMBER_HTML_FULL
    detail_html_2 = MEMBER_HTML_NO_EMAIL

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "companylist" in url:
            return _make_mock_response(list_html)
        elif "C_ID=560" in url:
            return _make_mock_response(detail_html_1)
        elif "C_ID=415" in url:
            return _make_mock_response(detail_html_2)
        return _make_mock_response("<html></html>", 404)

    mock_client.get = mock_get

    with patch("workers.micro.industry_directory_scraper._build_client", return_value=mock_client):
        with patch("workers.micro.industry_directory_scraper.asyncio.sleep", new_callable=AsyncMock):
            entries = await scrape_jdmia_members(crawl_delay=0)

    assert len(entries) == 2
    assert entries[0].company_name == "ＩＳＫ株式会社"
    assert entries[0].email == "isk@isk-jp.com"
    assert entries[1].company_name == "テスト金型工業株式会社"
    assert entries[1].email == ""


@pytest.mark.asyncio
async def test_scrape_jdmia_members_list_fetch_failure():
    """一覧ページの取得失敗時は空リストを返す。"""
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("workers.micro.industry_directory_scraper._build_client", return_value=mock_client):
        entries = await scrape_jdmia_members(crawl_delay=0)

    assert entries == []


@pytest.mark.asyncio
async def test_scrape_jdmia_members_detail_http_error_skipped():
    """詳細ページが404の場合はスキップして他のエントリは継続取得する。"""
    import httpx

    list_html = """
    <html><body>
    <a href="member.php?C_ID=560">ＩＳＫ(株)鋳ダイ</a>
    <a href="member.php?C_ID=415">(株)ISデザインプラ他</a>
    </body></html>
    """

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def mock_get(url, **kwargs):
        if "companylist" in url:
            return _make_mock_response(list_html)
        elif "C_ID=560" in url:
            # 560 は 404 エラー
            err_resp = MagicMock()
            err_resp.status_code = 404
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=err_resp)
        elif "C_ID=415" in url:
            return _make_mock_response(MEMBER_HTML_NO_EMAIL)
        return _make_mock_response("", 500)

    mock_client.get = mock_get

    with patch("workers.micro.industry_directory_scraper._build_client", return_value=mock_client):
        with patch("workers.micro.industry_directory_scraper.asyncio.sleep", new_callable=AsyncMock):
            entries = await scrape_jdmia_members(crawl_delay=0)

    # 560 はスキップ、415 は取得成功
    assert len(entries) == 1
    assert entries[0].company_name == "テスト金型工業株式会社"


@pytest.mark.asyncio
async def test_scrape_jdmia_members_max_companies():
    """max_companies で件数制限が効く。"""
    list_html = """
    <html><body>
    <a href="member.php?C_ID=1">企業A プラ</a>
    <a href="member.php?C_ID=2">企業B プレス</a>
    <a href="member.php?C_ID=3">企業C ダイ</a>
    </body></html>
    """

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    requested_ids: list[str] = []

    async def mock_get(url, **kwargs):
        if "companylist" in url:
            return _make_mock_response(list_html)
        for cid in ["1", "2", "3"]:
            if f"C_ID={cid}" in url:
                requested_ids.append(cid)
                html = f"""
                <html><body>
                <div class="companyInfo" id="content">
                  <h1 id="Pttl">企業{cid}</h1>
                  <dl class="company-information">
                    <dt>所在地/連絡先</dt>
                    <dd>〒000-000{cid}<br/>東京都テスト市{cid}<br/>TEL：0{cid}-0000-0000</dd>
                  </dl>
                </div>
                </body></html>
                """
                return _make_mock_response(html)
        return _make_mock_response("", 404)

    mock_client.get = mock_get

    with patch("workers.micro.industry_directory_scraper._build_client", return_value=mock_client):
        with patch("workers.micro.industry_directory_scraper.asyncio.sleep", new_callable=AsyncMock):
            entries = await scrape_jdmia_members(max_companies=2, crawl_delay=0)

    assert len(entries) == 2
    # 3件目は取得されない
    assert "3" not in requested_ids

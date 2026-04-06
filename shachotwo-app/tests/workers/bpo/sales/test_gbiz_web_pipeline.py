"""gbiz_detail_batch の step2_web_search_and_extract テスト。"""
import json
import os
import pytest
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.sales.gbiz_detail_batch import (
    step2_web_search_and_extract,
    summarize,
    _save_progress,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_companies(n: int) -> list[dict]:
    return [
        {
            "corporate_number": str(i).zfill(13),
            "name": f"テスト製作所{i}",
            "location": "愛知県名古屋市",
            "sub_industry": "金属加工",
        }
        for i in range(1, n + 1)
    ]


# ---------------------------------------------------------------------------
# step2_web_search_and_extract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step2_web_search_basic():
    """正常系: Web検索→詳細抽出が全社分実行され結果が返る。"""
    companies = _make_companies(3)

    async def mock_batch_search(companies, concurrency=3, delay=2.0):
        for c in companies:
            if not c.get("website_url"):
                c["website_url"] = f"https://{c['name'].lower()}.co.jp"
        return companies

    async def mock_extract_details(company_name, website_url, timeout=10.0):
        return {
            "emails": [f"info@{company_name}.co.jp"],
            "phone": "052-111-2222",
            "fax": "052-111-2223",
            "employee_count": 50,
            "business_description": "精密金属加工",
            "contact_form_url": f"{website_url}/contact",
            "address": "愛知県名古屋市中区1-2-3",
            "error": None,
        }

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        progress_file = f.name

    try:
        with (
            patch(
                "workers.bpo.sales.gbiz_detail_batch.batch_search_websites",
                side_effect=mock_batch_search,
            ),
            patch(
                "workers.bpo.sales.gbiz_detail_batch.extract_company_details",
                side_effect=mock_extract_details,
            ),
        ):
            results = await step2_web_search_and_extract(
                companies,
                progress_file=progress_file,
                concurrency=2,
                delay=0.0,
            )
    finally:
        if os.path.exists(progress_file):
            os.unlink(progress_file)

    assert len(results) == 3
    for r in results:
        assert r.get("website_url")
        assert r.get("web_fetched") is not None


@pytest.mark.asyncio
async def test_step2_skips_done_companies():
    """中断再開: 既に web_fetched=True の企業はスキップされる。"""
    companies = _make_companies(3)

    # 1社目を完了済みにする
    done_company = {**companies[0], "web_fetched": True, "website_url": "https://done.co.jp"}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump([done_company], f, ensure_ascii=False)
        progress_file = f.name

    search_calls: list[str] = []

    async def mock_batch_search(companies_list, concurrency=3, delay=2.0):
        for c in companies_list:
            search_calls.append(c["name"])
            if not c.get("website_url"):
                c["website_url"] = f"https://{c['name']}.co.jp"
        return companies_list

    async def mock_extract_details(company_name, website_url, timeout=10.0):
        return {
            "emails": [],
            "phone": "",
            "fax": "",
            "employee_count": None,
            "business_description": "",
            "contact_form_url": "",
            "address": "",
            "error": None,
        }

    try:
        with (
            patch(
                "workers.micro.web_searcher.batch_search_websites",
                side_effect=mock_batch_search,
            ),
            patch(
                "workers.micro.contact_extractor.extract_company_details",
                side_effect=mock_extract_details,
            ),
        ):
            results = await step2_web_search_and_extract(
                companies,
                progress_file=progress_file,
                concurrency=2,
                delay=0.0,
            )
    finally:
        if os.path.exists(progress_file):
            os.unlink(progress_file)

    # 完了済み1社 + 新規2社 = 合計3社
    assert len(results) == 3

    # 完了済み企業は検索対象外
    assert companies[0]["name"] not in search_calls


@pytest.mark.asyncio
async def test_step2_empty_companies():
    """空リストを渡しても正常に返る。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        progress_file = f.name

    try:
        results = await step2_web_search_and_extract(
            [],
            progress_file=progress_file,
            concurrency=3,
            delay=0.0,
        )
    finally:
        if os.path.exists(progress_file):
            os.unlink(progress_file)

    assert results == []


@pytest.mark.asyncio
async def test_step2_preserves_company_url_as_website_url():
    """gBizINFOで取得済みの company_url は website_url として引き継がれる。"""
    companies = [
        {
            "corporate_number": "1234567890123",
            "name": "既存URL会社",
            "location": "大阪府大阪市",
            "sub_industry": "機械製造",
            "company_url": "https://existing-url.co.jp",  # gBizINFOで取得済み
        }
    ]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        progress_file = f.name

    searched_names: list[str] = []

    async def mock_batch_search(companies_list, concurrency=3, delay=2.0):
        # company_url が website_url にコピーされているのでスキップされるはず
        for c in companies_list:
            if not c.get("website_url"):
                searched_names.append(c["name"])
                c["website_url"] = "https://fallback.co.jp"
        return companies_list

    async def mock_extract_details(company_name, website_url, timeout=10.0):
        return {
            "emails": ["info@existing-url.co.jp"],
            "phone": "06-1111-2222",
            "fax": "",
            "employee_count": 30,
            "business_description": "産業機械製造",
            "contact_form_url": "",
            "address": "大阪府大阪市北区1-1-1",
            "error": None,
        }

    try:
        with (
            patch(
                "workers.micro.web_searcher.batch_search_websites",
                side_effect=mock_batch_search,
            ),
            patch(
                "workers.micro.contact_extractor.extract_company_details",
                side_effect=mock_extract_details,
            ),
        ):
            results = await step2_web_search_and_extract(
                companies,
                progress_file=progress_file,
                concurrency=1,
                delay=0.0,
            )
    finally:
        if os.path.exists(progress_file):
            os.unlink(progress_file)

    assert len(results) == 1
    # company_url が website_url として引き継がれているので再検索不要
    assert "既存URL会社" not in searched_names
    assert results[0]["website_url"] == "https://existing-url.co.jp"


# ---------------------------------------------------------------------------
# summarize（出力確認）
# ---------------------------------------------------------------------------

def test_summarize_counts(capsys):
    """summarize が HP有り・メール有りを正しく集計して出力する。"""
    results = [
        {
            "corporate_number": "1111111111111",
            "name": "A社",
            "sub_industry": "金属加工",
            "website_url": "https://a.co.jp",
            "emails": ["info@a.co.jp"],
        },
        {
            "corporate_number": "2222222222222",
            "name": "B社",
            "sub_industry": "金属加工",
            "website_url": "",
            "emails": [],
        },
        {
            "corporate_number": "3333333333333",
            "name": "C社",
            "sub_industry": "樹脂加工",
            "website_url": "https://c.co.jp",
            "emails": [],
        },
    ]
    summarize(results)
    captured = capsys.readouterr()

    assert "合計: 3社" in captured.out
    assert "HP有り: 2社" in captured.out
    assert "メール有り: 1社" in captured.out
    assert "金属加工" in captured.out
    assert "樹脂加工" in captured.out


# ---------------------------------------------------------------------------
# _save_progress
# ---------------------------------------------------------------------------

def test_save_progress_creates_file():
    """_save_progress が JSON ファイルを作成する。"""
    results = [{"corporate_number": "1234567890123", "name": "テスト会社"}]

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = os.path.join(tmpdir, "sub", "output.json")
        _save_progress(results, filepath)

        assert os.path.exists(filepath)
        with open(filepath, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded[0]["name"] == "テスト会社"


def test_save_progress_overwrites():
    """_save_progress が既存ファイルを上書きする。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump([{"name": "旧データ"}], f, ensure_ascii=False)
        filepath = f.name

    try:
        _save_progress([{"name": "新データ"}], filepath)
        with open(filepath, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded[0]["name"] == "新データ"
    finally:
        os.unlink(filepath)

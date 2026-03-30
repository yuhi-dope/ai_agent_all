"""gBizINFO詳細APIバッチ取得スクリプト。

一覧APIで取得した法人番号を使い、詳細APIからHP(company_url)・従業員数・資本金等を取得する。
レート制限を守り（1リクエスト/秒）、進捗をJSONファイルに保存して中断・再開可能。

使い方:
    python -m workers.bpo.sales.gbiz_detail_batch \
        --token YOUR_API_TOKEN \
        --output data/manufacturing_leads.json \
        --delay 1.0

約14,000社 × 1秒/件 = 約4時間（バックグラウンド実行推奨）
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workers.connector.gbizinfo import MANUFACTURING_KEYWORDS
from workers.micro.web_searcher import batch_search_websites
from workers.micro.contact_extractor import extract_company_details

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://info.gbiz.go.jp/hojin/v1/hojin"

# サブ業種別検索キーワード
SEARCH_KEYWORDS: dict[str, list[str]] = {
    "金属加工": [
        "金属加工", "金属製品", "プレス", "切削", "鍛造", "鋳造", "板金",
        "めっき", "メッキ", "溶接", "研磨", "熱処理", "ダイカスト",
        "鋼材", "ステンレス", "アルミ", "金型", "ボルト", "ナット",
        "ねじ", "バネ", "パイプ", "線材", "鋳物", "鍛工", "刃物",
        "旋盤", "マシニング", "鉄工", "製缶",
    ],
    "樹脂加工": [
        "樹脂", "プラスチック", "成形", "射出成形", "ゴム",
        "シリコン", "パッキン", "シール", "フィルム", "チューブ", "容器製造",
    ],
    "機械製造": ["機械製造", "産業機械", "工作機械", "精密", "装置", "治具", "ポンプ"],
    "電子部品": ["電子部品", "半導体", "基板", "センサー", "コネクタ", "LED", "モーター"],
    "食品製造": ["食品製造", "食品加工", "飲料", "菓子", "冷凍食品", "調味料"],
    "化学製品": ["化学工業", "塗料", "化学製品", "接着剤", "インク"],
    "自動車部品": ["自動車部品", "車両部品", "ブレーキ", "ハーネス"],
}


async def step1_collect_corporate_numbers(
    token: str, delay: float = 0.5
) -> dict[str, list[dict]]:
    """Step 1: 一覧APIからサブ業種別に法人番号を収集する。"""
    headers = {"X-hojinInfo-api-token": token}
    results: dict[str, list[dict]] = {}
    global_seen: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for sub_ind, keywords in SEARCH_KEYWORDS.items():
            companies: list[dict] = []
            for kw in keywords:
                backoff = max(delay, 2.0)
                for attempt in range(3):
                    try:
                        resp = await client.get(
                            BASE_URL, params={"name": kw, "limit": 500}, headers=headers
                        )
                        if resp.status_code == 200:
                            for h in resp.json().get("hojin-infos", []):
                                cn = h.get("corporate_number", "")
                                status = h.get("status", "")
                                if cn and cn not in global_seen and status != "閉鎖":
                                    global_seen.add(cn)
                                    companies.append({
                                        "corporate_number": cn,
                                        "name": h.get("name", ""),
                                        "location": h.get("location", ""),
                                        "sub_industry": sub_ind,
                                    })
                            break
                        elif resp.status_code == 429:
                            wait = backoff * (2 ** attempt)
                            logger.warning(f"レート制限 ({kw}) — {wait:.0f}秒待機 (attempt {attempt+1}/3)")
                            await asyncio.sleep(wait)
                        else:
                            logger.warning(f"HTTP {resp.status_code} ({kw})")
                            break
                    except httpx.ReadTimeout:
                        wait = backoff * (2 ** attempt)
                        logger.warning(f"タイムアウト ({kw}) — {wait:.0f}秒待機 (attempt {attempt+1}/3)")
                        await asyncio.sleep(wait)
                    except Exception as e:
                        logger.warning(f"エラー ({kw}): {e}")
                        break
                await asyncio.sleep(delay)

            results[sub_ind] = companies
            logger.info(f"{sub_ind}: {len(companies)}社")

    total = sum(len(v) for v in results.values())
    logger.info(f"Step 1 完了: 合計 {total}社")
    return results


async def step2_web_search_and_extract(
    companies: list[dict],
    progress_file: str,
    concurrency: int = 10,
    delay: float = 0.3,
) -> list[dict]:
    """Step 2: Serper.dev でHP特定 → 並列で連絡先・詳細情報抽出。

    処理フロー:
    1. Serper.dev API で社名→HPのURL取得（並列10）
    2. HPをクロール → メール/電話/FAX/従業員数/事業内容/住所を抽出（並列10）
    3. 50件ごとに進捗保存（中断再開可能）

    Args:
        companies:     Step 1 で収集した企業リスト
        progress_file: 進捗保存先JSONファイルパス
        concurrency:   同時処理数（Serper APIは高速なので10推奨）
        delay:         Serper APIリクエスト間隔（秒。0.3で十分）

    Returns:
        website_url / emails / phone 等のフィールドが追加された企業リスト
    """
    from workers.micro.web_searcher import search_company_website

    # 既存の進捗を読み込み（中断再開対応）
    done: dict[str, dict] = {}
    if os.path.exists(progress_file):
        with open(progress_file, encoding="utf-8") as f:
            try:
                existing = json.load(f)
                done = {
                    c["corporate_number"]: c
                    for c in existing
                    if c.get("web_fetched") is not None
                }
            except json.JSONDecodeError:
                pass
        logger.info(f"既存進捗（Web取得済み）: {len(done)}社")

    results: list[dict] = list(done.values())
    remaining = [c for c in companies if c["corporate_number"] not in done]
    logger.info(f"残り: {len(remaining)}社")

    if not remaining:
        return results

    # ---- 一括処理: HP検索 → 詳細抽出を1社ずつ並列実行 ----
    logger.info(f"=== Step 2: HP検索+詳細抽出 (並列{concurrency}, {len(remaining)}社) ===")

    sem = asyncio.Semaphore(concurrency)
    search_sem = asyncio.Semaphore(concurrency)  # Serper API用
    processed_count = 0
    lock = asyncio.Lock()

    async def _process_one(company: dict) -> dict:
        nonlocal processed_count
        name = company.get("name", "")
        location = company.get("location", "")

        # Step 2-1: HP検索（Serper API）
        website_url = company.get("website_url", "") or company.get("company_url", "")
        if not website_url:
            async with search_sem:
                try:
                    website_url = await search_company_website(name, location) or ""
                except Exception as e:
                    logger.debug(f"検索エラー ({name}): {e}")
                    website_url = ""
                await asyncio.sleep(delay)

        company["website_url"] = website_url

        # Step 2-2: HP詳細抽出
        if website_url:
            async with sem:
                try:
                    details = await extract_company_details(
                        company_name=name,
                        website_url=website_url,
                    )
                    company.update({
                        "emails": details.get("emails", []),
                        "phone": details.get("phone", "") or company.get("phone", ""),
                        "fax": details.get("fax", ""),
                        "employee_count": details.get("employee_count") or company.get("employee_number"),
                        "business_description": details.get("business_description", ""),
                        "contact_form_url": details.get("contact_form_url", ""),
                        "address": details.get("address", "") or company.get("location", ""),
                        "web_fetched": True,
                        "web_error": details.get("error", ""),
                    })
                except Exception as e:
                    company.update({"emails": [], "web_fetched": False, "web_error": str(e)})
        else:
            company.update({"emails": [], "web_fetched": False, "web_error": "HP見つからず"})

        # 進捗カウント
        async with lock:
            processed_count += 1
            if processed_count % 50 == 0:
                all_so_far = results + [company]
                _save_progress(all_so_far, progress_file)
                hp_count = sum(1 for r in all_so_far if r.get("website_url"))
                email_count = sum(1 for r in all_so_far if r.get("emails"))
                phone_count = sum(1 for r in all_so_far if r.get("phone"))
                logger.info(
                    f"進捗: {processed_count}/{len(remaining)} — "
                    f"HP: {hp_count} / メール: {email_count} / 電話: {phone_count}"
                )

        return company

    # 全社を並列実行
    tasks = [_process_one(c) for c in remaining]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, dict):
            results.append(item)
        else:
            logger.warning(f"タスク例外: {item}")

    _save_progress(results, progress_file)

    with_url = sum(1 for r in results if r.get("website_url"))
    with_email = sum(1 for r in results if r.get("emails"))
    with_phone = sum(1 for r in results if r.get("phone"))
    logger.info(
        f"Step 2 完了: 合計={len(results)}社 / "
        f"HP={with_url} / メール={with_email} / 電話={with_phone}"
    )
    return results


async def step2_fetch_details(
    token: str,
    companies: list[dict],
    progress_file: str,
    delay: float = 1.0,
) -> list[dict]:
    """Step 2 (旧): 詳細APIから HP / 従業員数 / 資本金等を取得する。

    .. deprecated::
        gBizINFO詳細APIではHP情報がほぼ取得できないため、
        代わりに :func:`step2_web_search_and_extract` を使用すること。
    """
    headers = {"X-hojinInfo-api-token": token}

    done: dict[str, dict] = {}
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            existing = json.load(f)
            done = {c["corporate_number"]: c for c in existing if "detail_fetched" in c}
        logger.info(f"既存進捗: {len(done)}社")

    results: list[dict] = list(done.values())
    remaining = [c for c in companies if c["corporate_number"] not in done]
    logger.info(f"残り: {len(remaining)}社")

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, company in enumerate(remaining):
            cn = company["corporate_number"]
            for attempt in range(3):
                try:
                    resp = await client.get(f"{BASE_URL}/{cn}", headers=headers)
                    if resp.status_code == 200:
                        infos = resp.json().get("hojin-infos", [])
                        if infos:
                            detail = infos[0]
                            company.update({
                                "company_url": detail.get("company_url", ""),
                                "employee_number": detail.get("employee_number"),
                                "capital_stock": detail.get("capital_stock"),
                                "representative_name": detail.get("representative_name", ""),
                                "business_summary": detail.get("business_summary", ""),
                                "date_of_establishment": detail.get("date_of_establishment", ""),
                                "detail_fetched": True,
                            })
                        break
                    elif resp.status_code == 429:
                        wait = delay * (2 ** (attempt + 2))
                        logger.warning(f"レート制限 ({cn}) — {wait:.0f}秒待機 (attempt {attempt+1}/3)")
                        await asyncio.sleep(wait)
                    else:
                        company["detail_fetched"] = False
                        company["detail_error"] = f"HTTP {resp.status_code}"
                        break
                except httpx.ReadTimeout:
                    wait = delay * (2 ** (attempt + 2))
                    logger.warning(f"タイムアウト ({cn}) — {wait:.0f}秒待機 (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait)
                except Exception as e:
                    company["detail_fetched"] = False
                    company["detail_error"] = str(e)
                    break

            results.append(company)

            if (i + 1) % 100 == 0:
                _save_progress(results, progress_file)
                logger.info(f"進捗: {i + 1}/{len(remaining)} ({len([r for r in results if r.get('company_url')])} HP有り)")

            await asyncio.sleep(delay)

    _save_progress(results, progress_file)
    return results


def _save_progress(results: list[dict], filepath: str) -> None:
    """進捗をJSONファイルに保存。"""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)


def summarize(results: list[dict]) -> None:
    """集計サマリーを出力。"""
    total = len(results)
    # website_url（Web検索結果）と company_url（gBizINFO）の両方を HP有りとして集計
    with_url = sum(1 for r in results if r.get("website_url") or r.get("company_url"))
    with_email = sum(1 for r in results if r.get("emails"))
    with_employee = sum(
        1 for r in results if r.get("employee_count") or r.get("employee_number")
    )
    with_capital = sum(1 for r in results if r.get("capital_stock"))

    by_sub: dict[str, dict] = {}
    for r in results:
        si = r.get("sub_industry", "不明")
        if si not in by_sub:
            by_sub[si] = {"total": 0, "with_url": 0, "with_email": 0}
        by_sub[si]["total"] += 1
        if r.get("website_url") or r.get("company_url"):
            by_sub[si]["with_url"] += 1
        if r.get("emails"):
            by_sub[si]["with_email"] += 1

    print("\n" + "=" * 60)
    print("gBizINFO 製造業リスト集計結果")
    print("=" * 60)
    print(f"合計: {total}社")
    print(f"HP有り: {with_url}社 ({with_url / max(total, 1) * 100:.1f}%)")
    print(f"メール有り: {with_email}社 ({with_email / max(total, 1) * 100:.1f}%)")
    print(f"従業員数有り: {with_employee}社")
    print(f"資本金有り: {with_capital}社")
    print()
    print("サブ業種別:")
    for si, data in sorted(by_sub.items(), key=lambda x: -x[1]["total"]):
        url_pct = data["with_url"] / max(data["total"], 1) * 100
        email_pct = data["with_email"] / max(data["total"], 1) * 100
        print(
            f"  {si}: {data['total']}社 "
            f"(HP {data['with_url']}社/{url_pct:.0f}% "
            f"/ メール {data['with_email']}社/{email_pct:.0f}%)"
        )
    print("=" * 60)


async def main(token: str, output: str, delay: float, concurrency: int) -> None:
    """メイン処理。"""
    # Step 1: 法人番号収集
    logger.info("=== Step 1: 法人番号収集 ===")
    by_sub = await step1_collect_corporate_numbers(token, delay=max(delay * 0.5, 0.3))

    all_companies: list[dict] = []
    for companies in by_sub.values():
        all_companies.extend(companies)

    logger.info(f"合計 {len(all_companies)}社の法人番号を収集")

    # Step 2: Web検索でHP特定 → 連絡先抽出
    logger.info("=== Step 2: Web検索→HP→連絡先抽出 ===")
    results = await step2_web_search_and_extract(
        all_companies,
        progress_file=output,
        concurrency=concurrency,
        delay=delay,
    )

    # サマリー
    summarize(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gBizINFO 製造業リスト一括取得（Web検索版）")
    parser.add_argument("--token", required=True, help="gBizINFO API トークン")
    parser.add_argument(
        "--output", default="data/manufacturing_leads.json", help="出力ファイルパス"
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Serper APIリクエスト間隔（秒）。デフォルト0.3"
    )
    parser.add_argument(
        "--concurrency", type=int, default=10,
        help="同時処理数。デフォルト10（HP検索+抽出を並列実行）"
    )
    args = parser.parse_args()

    asyncio.run(main(args.token, args.output, args.delay, args.concurrency))

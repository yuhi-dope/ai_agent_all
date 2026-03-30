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
                    await asyncio.sleep(delay)
                except httpx.ReadTimeout:
                    logger.warning(f"タイムアウト: {kw} — 5秒待って継続")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.warning(f"エラー ({kw}): {e}")
                    await asyncio.sleep(2)

            results[sub_ind] = companies
            logger.info(f"{sub_ind}: {len(companies)}社")

    total = sum(len(v) for v in results.values())
    logger.info(f"Step 1 完了: 合計 {total}社")
    return results


async def step2_fetch_details(
    token: str,
    companies: list[dict],
    progress_file: str,
    delay: float = 1.0,
) -> list[dict]:
    """Step 2: 詳細APIから HP / 従業員数 / 資本金等を取得する。

    進捗をファイルに保存し、中断・再開可能。
    """
    headers = {"X-hojinInfo-api-token": token}

    # 既存の進捗を読み込み
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
                elif resp.status_code == 429:
                    logger.warning(f"レート制限 — 30秒待機")
                    await asyncio.sleep(30)
                    continue
                else:
                    company["detail_fetched"] = False
                    company["detail_error"] = f"HTTP {resp.status_code}"
            except httpx.ReadTimeout:
                logger.warning(f"タイムアウト ({cn}) — 10秒待機")
                company["detail_fetched"] = False
                company["detail_error"] = "timeout"
                await asyncio.sleep(10)
            except Exception as e:
                company["detail_fetched"] = False
                company["detail_error"] = str(e)

            results.append(company)

            # 100件ごとに進捗保存
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
    with_url = sum(1 for r in results if r.get("company_url"))
    with_employee = sum(1 for r in results if r.get("employee_number"))
    with_capital = sum(1 for r in results if r.get("capital_stock"))

    by_sub = {}
    for r in results:
        si = r.get("sub_industry", "不明")
        if si not in by_sub:
            by_sub[si] = {"total": 0, "with_url": 0}
        by_sub[si]["total"] += 1
        if r.get("company_url"):
            by_sub[si]["with_url"] += 1

    print("\n" + "=" * 60)
    print("gBizINFO 製造業リスト集計結果")
    print("=" * 60)
    print(f"合計: {total}社")
    print(f"HP有り: {with_url}社 ({with_url/max(total,1)*100:.1f}%)")
    print(f"従業員数有り: {with_employee}社")
    print(f"資本金有り: {with_capital}社")
    print()
    print("サブ業種別:")
    for si, data in sorted(by_sub.items(), key=lambda x: -x[1]["total"]):
        pct = data["with_url"] / max(data["total"], 1) * 100
        print(f"  {si}: {data['total']}社 (HP有り {data['with_url']}社 / {pct:.0f}%)")
    print("=" * 60)


async def main(token: str, output: str, delay: float) -> None:
    """メイン処理。"""
    # Step 1: 法人番号収集
    logger.info("=== Step 1: 法人番号収集 ===")
    by_sub = await step1_collect_corporate_numbers(token, delay=max(delay * 0.5, 0.3))

    all_companies = []
    for companies in by_sub.values():
        all_companies.extend(companies)

    logger.info(f"合計 {len(all_companies)}社の法人番号を収集")

    # Step 2: 詳細取得
    logger.info("=== Step 2: 詳細API取得（HP/従業員数/資本金）===")
    results = await step2_fetch_details(token, all_companies, output, delay=delay)

    # サマリー
    summarize(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="gBizINFO 製造業リスト一括取得")
    parser.add_argument("--token", required=True, help="gBizINFO API トークン")
    parser.add_argument("--output", default="data/manufacturing_leads.json", help="出力ファイルパス")
    parser.add_argument("--delay", type=float, default=1.0, help="APIリクエスト間隔（秒）")
    args = parser.parse_args()

    asyncio.run(main(args.token, args.output, args.delay))

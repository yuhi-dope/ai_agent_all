"""kintone 製造業アプリ(133)からTSRフィールド付きで全件取得（並列版）。

Usage:
    python scripts/kintone_fetch_manufacturing_tsr.py
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

SUBDOMAIN = "beavers"
APP_ID = "133"
API_TOKEN = "55vi4azteJQOKjEkDONvs5VCxck5jtrKl4rjtCiA"
MAX_LIMIT = 500
WORKERS = 10

# canonical key → kintone フィールドコード
FIELD_TO_CANONICAL = {
    "HoujinBango": "corporate_number",
    "Master_Jigyousha": "name",
    "Master_KasihsaHP": "website_url",
    "Master_DaihyouBangou": "representative_phone",
    "Master_YubinBangou": "zip_code",
    "Master_HonshaJusho": "address",
    "Master_Todoufuken": "prefecture",
    "Master_Shihonkin": "capital_stock",
    "Master_Shainsu": "employee_count",
    "Master_SetsuritsuNen": "establishment_year",
    "kigyo_G": "company_genre",
    "カテゴリ": "category",
    "タグ": "tag",
    "大分類_TSR": "tsr_category_large",
    "中分類_TSR": "tsr_category_medium",
    "小分類_TSR": "tsr_category_small",
    "細分類_TSR": "tsr_category_detail",
    "営業種目_TSR": "tsr_business_items",
    "仕入先_TSR": "tsr_suppliers",
    "販売先_TSR": "tsr_customers",
    "代表者_TSR": "tsr_representative",
    "売上_直近期_TSR": "tsr_revenue_latest",
    "純利益_直近期_TSR": "tsr_profit_latest",
}

HEADERS = {"X-Cybozu-API-Token": API_TOKEN}
BASE_URL = f"https://{SUBDOMAIN}.cybozu.com/k/v1/records.json"

print_lock = Lock()
counter = {"done": 0}


def cell_value(cell):
    if cell is None or not isinstance(cell, dict):
        return None
    return cell.get("value")


def record_to_flat(rec: dict) -> dict:
    out = {}
    for kintone_code, canonical in FIELD_TO_CANONICAL.items():
        out[canonical] = cell_value(rec.get(kintone_code))
    out["kintone_id"] = cell_value(rec.get("$id"))
    return out


def fetch_range(range_start: int, range_end: int, worker_id: int) -> list[dict]:
    """$id が range_start < $id <= range_end の範囲を全件取得。"""
    records = []
    last_id = range_start
    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        query = f"$id > {last_id} and $id <= {range_end} order by $id asc limit {MAX_LIMIT}"
        resp = session.get(BASE_URL, params={"app": APP_ID, "query": query}, timeout=30)
        if resp.status_code != 200:
            with print_lock:
                print(f"  [W{worker_id}] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr, flush=True)
            resp.raise_for_status()

        batch = resp.json().get("records", [])
        if not batch:
            break

        for rec in batch:
            records.append(record_to_flat(rec))
            rid = cell_value(rec.get("$id"))
            if rid:
                try:
                    rid_int = int(rid)
                    if rid_int > last_id:
                        last_id = rid_int
                except (ValueError, TypeError):
                    pass

        counter["done"] += len(batch)
        with print_lock:
            print(f"  [W{worker_id}] {len(records)} 件取得 (total≈{counter['done']})", flush=True)

        if len(batch) < MAX_LIMIT:
            break

    return records


def main():
    t0 = time.time()
    print(f"kintone app {APP_ID} ({SUBDOMAIN}) — 並列{WORKERS}ワーカーで全件取得開始...", flush=True)

    # $id 範囲を分割
    min_id = 1634  # 1635の1つ前
    max_id = 811932
    chunk = (max_id - min_id) // WORKERS

    ranges = []
    for i in range(WORKERS):
        start = min_id + chunk * i
        end = min_id + chunk * (i + 1) if i < WORKERS - 1 else max_id
        ranges.append((start, end, i))

    all_records = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_range, s, e, w): w for s, e, w in ranges}
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                result = future.result()
                all_records.extend(result)
                print(f"  [W{worker_id}] 完了: {len(result)} 件", flush=True)
            except Exception as exc:
                print(f"  [W{worker_id}] エラー: {exc}", file=sys.stderr, flush=True)

    # $id順にソート
    all_records.sort(key=lambda r: int(r.get("kintone_id") or 0))

    elapsed = time.time() - t0
    output_path = "data/kintone_manufacturing_with_tsr.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=1)

    print(f"\n完了: {len(all_records)} 件を {output_path} に保存 ({elapsed:.1f}秒)", flush=True)

    # サンプル表示（TSRフィールドが入ってるレコード）
    for rec in all_records[:100]:
        if rec.get("tsr_category_large"):
            print("\nサンプル (TSRデータあり):", flush=True)
            for k, v in rec.items():
                if v:
                    print(f"  {k}: {str(v)[:100]}", flush=True)
            break


if __name__ == "__main__":
    main()

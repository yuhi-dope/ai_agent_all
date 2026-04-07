"""kintone app 133 から士業リードを取得（税理士・社労士・弁護士・行政書士・司法書士）。

タグフィールドで業種フィルタし、並列ワーカーで高速取得する。

Usage:
    # 全士業を一括取得
    python scripts/kintone_fetch_professional_leads.py

    # 業種を指定して取得
    python scripts/kintone_fetch_professional_leads.py --tags 税理士,社労士

    # 出力先を指定
    python scripts/kintone_fetch_professional_leads.py --output data/tax_leads.json --tags 税理士
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

SUBDOMAIN = "beavers"
APP_ID = "133"
API_TOKEN = "55vi4azteJQOKjEkDONvs5VCxck5jtrKl4rjtCiA"
MAX_LIMIT = 500
WORKERS = 10

# 士業タグ一覧
DEFAULT_TAGS = ["税理士", "社労士", "弁護士", "行政書士", "司法書士"]

# kintone フィールドコード → canonical キー
FIELD_TO_CANONICAL: dict[str, str] = {
    "HoujinBango": "corporate_number",
    "法人番号_重複確認": "corporate_number_alt",
    "Master_Jigyousha": "name",
    "事業者名_手入力": "name_manual",
    "Master_KasihsaHP": "website_url",
    "会社HP_手入力": "website_url_manual",
    "Master_DaihyouBangou": "phone",
    "代表番号_手入力": "phone_manual",
    "Master_YubinBangou": "zip_code",
    "Master_HonshaJusho": "address",
    "本社住所_手入力": "address_manual",
    "Master_Todoufuken": "prefecture",
    "本社都道府県_手入力": "prefecture_manual",
    "代表者_手入力": "representative",
    "代表者_TSR": "representative_tsr",
    "Master_Shainsu": "employee_count",
    "社員数_TSR": "employee_count_tsr",
    "Master_Shihonkin": "capital_stock",
    "資本金_TSR": "capital_stock_tsr",
    "売上_直近期_TSR": "revenue_latest",
    "純利益_直近期_TSR": "profit_latest",
    "大分類_TSR": "tsr_category_large",
    "中分類_TSR": "tsr_category_medium",
    "細分類_TSR": "tsr_category_detail",
    "タグ": "tag",
    "企業ジャンル": "company_genre",
    "カテゴリ": "category",
    "概況_TSR": "tsr_overview",
}

HEADERS = {"X-Cybozu-API-Token": API_TOKEN}
BASE_URL = f"https://{SUBDOMAIN}.cybozu.com/k/v1/records.json"

print_lock = Lock()
counter: dict[str, int] = {"done": 0}


def cell_value(cell: object) -> object:
    if cell is None or not isinstance(cell, dict):
        return None
    return cell.get("value")


def record_to_flat(rec: dict) -> dict:
    out: dict[str, object] = {}
    for kintone_code, canonical in FIELD_TO_CANONICAL.items():
        val = cell_value(rec.get(kintone_code))
        # corporate_number は master を優先
        if canonical == "corporate_number_alt":
            if not out.get("corporate_number"):
                out["corporate_number"] = val
        elif canonical == "name_manual":
            if not out.get("name"):
                out["name"] = val
        elif canonical == "website_url_manual":
            if not out.get("website_url"):
                out["website_url"] = val
        elif canonical == "phone_manual":
            if not out.get("phone"):
                out["phone"] = val
        elif canonical == "address_manual":
            if not out.get("address"):
                out["address"] = val
        elif canonical == "prefecture_manual":
            if not out.get("prefecture"):
                out["prefecture"] = val
        elif canonical == "representative_tsr":
            if not out.get("representative"):
                out["representative"] = val
        elif canonical == "employee_count_tsr":
            if not out.get("employee_count"):
                out["employee_count"] = val
        elif canonical == "capital_stock_tsr":
            if not out.get("capital_stock"):
                out["capital_stock"] = val
        else:
            out[canonical] = val
    out["kintone_id"] = cell_value(rec.get("$id"))
    return out


def build_tag_filter(tags: list[str]) -> str:
    quoted = ", ".join(f'"{t}"' for t in tags)
    return f"タグ in ({quoted})"


def fetch_range(
    range_start: int,
    range_end: int,
    worker_id: int,
    tag_filter: str,
) -> list[dict]:
    """$id が range_start < $id <= range_end かつ tag_filter に一致するレコードを全件取得。"""
    records: list[dict] = []
    last_id = range_start
    session = requests.Session()
    session.headers.update(HEADERS)

    while True:
        query = (
            f"{tag_filter} and $id > {last_id} and $id <= {range_end}"
            f" order by $id asc limit {MAX_LIMIT}"
        )
        resp = session.get(BASE_URL, params={"app": APP_ID, "query": query}, timeout=60)
        if resp.status_code != 200:
            with print_lock:
                print(
                    f"  [W{worker_id}] HTTP {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                    flush=True,
                )
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
            print(
                f"  [W{worker_id}] +{len(batch)} 件 (合計≈{counter['done']})",
                flush=True,
            )

        if len(batch) < MAX_LIMIT:
            break

    return records


def get_id_range(tag_filter: str) -> tuple[int, int]:
    """タグフィルタに一致するレコードの min/max $id を取得。"""
    session = requests.Session()
    session.headers.update(HEADERS)

    r_min = session.get(
        BASE_URL,
        params={"app": APP_ID, "query": f"{tag_filter} order by $id asc limit 1"},
        timeout=30,
    )
    r_max = session.get(
        BASE_URL,
        params={"app": APP_ID, "query": f"{tag_filter} order by $id desc limit 1"},
        timeout=30,
    )
    r_min.raise_for_status()
    r_max.raise_for_status()

    min_records = r_min.json().get("records", [])
    max_records = r_max.json().get("records", [])

    if not min_records or not max_records:
        return 0, 0

    min_id = int(cell_value(min_records[0].get("$id")) or 0)
    max_id = int(cell_value(max_records[0].get("$id")) or 0)
    return min_id - 1, max_id  # range_start は min の1つ前


def main() -> None:
    parser = argparse.ArgumentParser(description="kintone app 133 から士業リードを取得")
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help=f"取得する業種タグ（カンマ区切り）。デフォルト: {','.join(DEFAULT_TAGS)}",
    )
    parser.add_argument(
        "--output",
        default="data/professional_leads.json",
        help="出力先JSONファイルパス",
    )
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    output_path = args.output
    tag_filter = build_tag_filter(tags)

    t0 = time.time()
    print(f"kintone app {APP_ID} ({SUBDOMAIN}) — 士業リスト取得開始", flush=True)
    print(f"  対象タグ: {tags}", flush=True)
    print(f"  フィルタ: {tag_filter}", flush=True)

    print("  $id 範囲を取得中...", flush=True)
    min_id, max_id = get_id_range(tag_filter)
    if max_id == 0:
        print("対象レコードが見つかりませんでした。", flush=True)
        return

    print(f"  $id 範囲: {min_id + 1} 〜 {max_id}", flush=True)

    chunk = max((max_id - min_id) // WORKERS, 1)
    ranges = []
    for i in range(WORKERS):
        start = min_id + chunk * i
        end = min_id + chunk * (i + 1) if i < WORKERS - 1 else max_id
        ranges.append((start, end, i))

    all_records: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_range, s, e, w, tag_filter): w for s, e, w in ranges
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                result = future.result()
                all_records.extend(result)
                print(f"  [W{worker_id}] 完了: {len(result)} 件", flush=True)
            except Exception as exc:
                print(
                    f"  [W{worker_id}] エラー: {exc}", file=sys.stderr, flush=True
                )

    # $id 順にソート
    all_records.sort(key=lambda r: int(r.get("kintone_id") or 0))

    elapsed = time.time() - t0

    # タグ別集計
    tag_counter: Counter[str] = Counter()
    for rec in all_records:
        tag_val = rec.get("tag")
        if isinstance(tag_val, list):
            for t in tag_val:
                tag_counter[str(t)] += 1
        elif tag_val:
            tag_counter[str(tag_val)] += 1

    print(f"\n=== 取得完了: {len(all_records)} 件 ({elapsed:.1f}秒) ===", flush=True)
    print("  タグ別内訳:", flush=True)
    for tag, cnt in tag_counter.most_common():
        print(f"    {tag}: {cnt} 件", flush=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=1)

    print(f"\n  → {output_path} に保存しました", flush=True)

    # サンプル表示
    if all_records:
        print("\nサンプルレコード:", flush=True)
        sample = all_records[0]
        for k, v in sample.items():
            if v:
                print(f"  {k}: {str(v)[:100]}", flush=True)


if __name__ == "__main__":
    main()

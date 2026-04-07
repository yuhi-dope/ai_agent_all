"""kintone JSONからleadsテーブルへupsert（製造業/建設業1億以上/士業4種）。

Usage:
    python scripts/kintone_upsert_leads.py
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from dotenv import load_dotenv
load_dotenv()

from db.supabase import get_service_client

COMPANY_ID = "86ea5be1-6121-4303-b8d4-84c26b7906b6"
INPUT_PATH = "data/kintone_manufacturing_with_tsr.json"
BATCH_SIZE = 200  # upsert バッチサイズ
WORKERS = 5

SHIGYO_TAGS = {"税理士", "社労士", "行政書士", "弁護士"}

print_lock = Lock()
stats = {"ok": 0, "skip": 0, "err": 0}


def safe_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def safe_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def classify_industry(rec: dict) -> str | None:
    """フィルタ条件に一致するレコードの industry を返す。一致しなければ None。"""
    tsr_large = rec.get("tsr_category_large") or ""
    tag = rec.get("tag") or ""
    revenue = safe_int(rec.get("tsr_revenue_latest")) or 0

    if tsr_large == "製造業":
        return "manufacturing"
    if tsr_large == "建設業" and revenue >= 100000:  # 1億円 = 100,000千円
        return "construction"
    if tag in SHIGYO_TAGS:
        return "professional_services"
    return None


def to_lead_row(rec: dict, industry: str) -> dict | None:
    corp = safe_str(rec.get("corporate_number"))
    name = safe_str(rec.get("name"))
    if not corp or not name:
        return None

    tag = safe_str(rec.get("tag"))
    sub_industry = safe_str(rec.get("tsr_category_medium")) or tag

    return {
        "company_id": COMPANY_ID,
        "company_name": name,
        "contact_phone": safe_str(rec.get("representative_phone")),
        "contact_email": None,
        "industry": industry,
        "employee_count": safe_int(rec.get("employee_count")),
        "source": "kintone",
        "source_detail": f"kintone_app133_{tag or industry}",
        "status": "new",
        "corporate_number": corp,
        "capital_stock": safe_int(rec.get("capital_stock")),
        "annual_revenue": safe_int(rec.get("tsr_revenue_latest")),
        "operating_profit": safe_int(rec.get("tsr_profit_latest")),
        "sub_industry": sub_industry,
        "prefecture": safe_str(rec.get("prefecture")),
        "city": None,
        "website_url": safe_str(rec.get("website_url")),
        "representative": safe_str(rec.get("tsr_representative")),
        "business_overview": safe_str(rec.get("tsr_business_items")),
        # TSR fields
        "tsr_category_large": safe_str(rec.get("tsr_category_large")),
        "tsr_category_medium": safe_str(rec.get("tsr_category_medium")),
        "tsr_category_small": safe_str(rec.get("tsr_category_small")),
        "tsr_category_detail": safe_str(rec.get("tsr_category_detail")),
        "tsr_business_items": safe_str(rec.get("tsr_business_items")),
        "tsr_suppliers": safe_str(rec.get("tsr_suppliers")),
        "tsr_customers": safe_str(rec.get("tsr_customers")),
        "tsr_representative": safe_str(rec.get("tsr_representative")),
        "representative_phone": safe_str(rec.get("representative_phone")),
        "tsr_revenue_latest": safe_int(rec.get("tsr_revenue_latest")),
        "tsr_profit_latest": safe_int(rec.get("tsr_profit_latest")),
    }


failed_rows: list[dict] = []
failed_lock = Lock()


def upsert_batch(rows: list[dict], batch_num: int) -> int:
    db = get_service_client()
    try:
        db.table("leads").upsert(
            rows,
            on_conflict="company_id,corporate_number",
        ).execute()
        return len(rows)
    except Exception as e:
        with failed_lock:
            failed_rows.extend(rows)
        with print_lock:
            stats["err"] += 1
            if stats["err"] <= 5:
                print(f"  [batch {batch_num}] エラー: {e}", file=sys.stderr, flush=True)
        return 0


def main():
    t0 = time.time()
    print(f"JSONファイル読み込み: {INPUT_PATH}", flush=True)
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    print(f"  総レコード: {len(all_data):,}", flush=True)

    # フィルタ & 変換
    rows = []
    industry_counts = {"manufacturing": 0, "construction": 0, "professional_services": 0}
    skipped = 0

    for rec in all_data:
        industry = classify_industry(rec)
        if not industry:
            continue
        row = to_lead_row(rec, industry)
        if row:
            rows.append(row)
            industry_counts[industry] += 1
        else:
            skipped += 1

    print(f"\n対象レコード: {len(rows):,} 件", flush=True)
    print(f"  製造業: {industry_counts['manufacturing']:,}", flush=True)
    print(f"  建設業(売上1億以上): {industry_counts['construction']:,}", flush=True)
    print(f"  士業4種: {industry_counts['professional_services']:,}", flush=True)
    print(f"  スキップ(法人番号/企業名なし): {skipped}", flush=True)

    # バッチ分割
    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    print(f"\nupsert開始: {len(batches)} バッチ × {BATCH_SIZE}件 (並列{WORKERS})", flush=True)

    total_ok = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(upsert_batch, batch, i): i
            for i, batch in enumerate(batches)
        }
        done_count = 0
        for future in as_completed(futures):
            ok = future.result()
            total_ok += ok
            done_count += 1
            if done_count % 50 == 0 or done_count == len(batches):
                elapsed = time.time() - t0
                print(f"  進捗: {done_count}/{len(batches)} バッチ ({total_ok:,} 件 upsert済, {elapsed:.0f}秒)", flush=True)

    elapsed = time.time() - t0
    print(f"\n完了: {total_ok:,} 件 upsert ({elapsed:.1f}秒)", flush=True)

    # リトライ
    if failed_rows:
        print(f"\nリトライ: {len(failed_rows):,} 件 (バッチサイズ50, 並列2)", flush=True)
        retry_batches = [failed_rows[i:i + 50] for i in range(0, len(failed_rows), 50)]
        retry_ok = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(upsert_batch, b, 9000 + i): i for i, b in enumerate(retry_batches)}
            for future in as_completed(futures):
                retry_ok += future.result()
        total_ok += retry_ok
        print(f"  リトライ結果: {retry_ok:,} 件追加 (合計 {total_ok:,} 件)", flush=True)


if __name__ == "__main__":
    main()

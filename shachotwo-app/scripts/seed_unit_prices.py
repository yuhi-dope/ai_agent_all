"""
unit_price_master 初期投入スクリプト

設計書PDFから抽出した単価データを unit_price_master に一括投入する。
これにより、次回以降の積算AIが過去実績ベースの単価推定を行えるようになる。

使い方:
  cd shachotwo-app
  python scripts/seed_unit_prices.py

  # 特定ディレクトリのみ:
  python scripts/seed_unit_prices.py --dir 建設実データ/東京国道

  # ドライラン（DBに書き込まない）:
  python scripts/seed_unit_prices.py --dry-run
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # pymupdf
from workers.bpo.construction.estimator import EstimationPipeline


def extract_design_text(pdf_path: str) -> str:
    """設計書PDFから設計内訳書部分のテキストを抽出"""
    doc = fitz.open(pdf_path)
    texts = []
    started = False
    for i in range(min(30, doc.page_count)):
        t = doc[i].get_text()
        if "設計内訳書" in t:
            started = True
        if started:
            texts.append(t)
            if len(texts) >= 15:
                break
    doc.close()
    return "\n".join(texts)


def find_pdfs(base_dir: str) -> list[str]:
    """PDFファイルを再帰的に検索"""
    pdfs = []
    for root, _, files in os.walk(base_dir):
        for f in sorted(files):
            if f.endswith(".pdf"):
                pdfs.append(os.path.join(root, f))
    return pdfs


async def extract_items(pipeline: EstimationPipeline, pdf_path: str) -> list:
    """PDFから数量+単価を抽出（DBモック）"""
    text = extract_design_text(pdf_path)
    if not text.strip():
        return []

    mock_db = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])

    with patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
        items = await pipeline.extract_quantities("seed", "seed", text)

    return items


def infer_region_from_path(pdf_path: str) -> str:
    """パスやファイル名から地域を推定"""
    fname = os.path.basename(pdf_path).lower()
    path_lower = pdf_path.lower()

    region_map = {
        "東京": "東京都", "品川": "東京都", "亀有": "東京都", "江東": "東京都",
        "板橋": "東京都", "万世橋": "東京都", "代々木": "東京都",
        "川崎": "神奈川県", "横浜": "神奈川県", "厚木": "神奈川県",
        "伊勢原": "神奈川県", "南町田": "東京都",
        "多治見": "岐阜県", "中津川": "岐阜県", "上松": "長野県",
        "木曽川": "長野県",
    }

    for keyword, region in region_map.items():
        if keyword in fname or keyword in path_lower:
            return region

    if "東京国道" in path_lower:
        return "東京都"
    if "川崎国道" in path_lower:
        return "神奈川県"

    return "不明"


def infer_fiscal_year(pdf_path: str) -> int:
    """ファイル名から年度を推定"""
    fname = os.path.basename(pdf_path)
    if "令和５" in fname or "令和5" in fname:
        return 2023
    if "令和６" in fname or "令和6" in fname or "R6" in fname or "Ｒ６" in fname:
        return 2024
    if "令和７" in fname or "令和7" in fname or "R7" in fname or "Ｒ７" in fname:
        return 2025
    return 2025  # デフォルト


async def main():
    parser = argparse.ArgumentParser(description="設計書PDFからunit_price_masterに単価データを投入")
    parser.add_argument("--dir", default="建設実データ", help="PDFディレクトリ")
    parser.add_argument("--dry-run", action="store_true", help="DBに書き込まない")
    parser.add_argument("--company-id", default=None, help="投入先のcompany_id（指定しなければNULL=共通データ）")
    args = parser.parse_args()

    pdfs = find_pdfs(args.dir)
    print(f"PDFファイル: {len(pdfs)}件")
    print(f"ドライラン: {'はい' if args.dry_run else 'いいえ'}")
    print(f"投入先: {'company_id=' + args.company_id if args.company_id else '共通データ（company_id=NULL）'}")
    print("=" * 80)

    pipeline = EstimationPipeline()
    all_records = []
    category_counts = Counter()

    for pdf_path in pdfs:
        fname = os.path.basename(pdf_path)
        region = infer_region_from_path(pdf_path)
        year = infer_fiscal_year(pdf_path)

        items = await extract_items(pipeline, pdf_path)

        if not items:
            print(f"  ⚠️  {fname[:40]:40s} → 0件（設計内訳書なし）")
            continue

        records = []
        for item in items:
            if not item.unit_price or item.unit_price <= 0:
                continue

            records.append({
                "company_id": args.company_id,
                "category": item.category,
                "subcategory": item.subcategory,
                "detail": item.detail,
                "specification": item.specification,
                "unit": item.unit,
                "unit_price": float(item.unit_price),
                "price_type": "composite",
                "region": region,
                "year": year,
                "source": "past_estimation",
                "source_detail": f"設計書: {fname}",
            })
            category_counts[item.category] += 1

        all_records.extend(records)
        print(f"  ✅  {fname[:40]:40s} → {len(records):3d}件 ({region}, {year}年度)")

    print()
    print("=" * 80)
    print(f"合計: {len(all_records)}件")
    print()
    print("工種別:")
    for cat, cnt in category_counts.most_common():
        print(f"  {cat:20s}: {cnt:4d}件")

    if args.dry_run:
        print()
        print("ドライランのためDBには投入しません。")
        print("実行するには --dry-run を外してください。")
        return

    # DB投入
    print()
    print("DBに投入中...")

    from dotenv import load_dotenv
    load_dotenv()
    from db.supabase import get_service_client

    client = get_service_client()

    # バッチ投入（100件ずつ）
    batch_size = 100
    inserted = 0
    for i in range(0, len(all_records), batch_size):
        batch = all_records[i:i + batch_size]
        try:
            client.table("unit_price_master").insert(batch).execute()
            inserted += len(batch)
            print(f"  {inserted}/{len(all_records)}件投入完了")
        except Exception as e:
            print(f"  ❌ バッチ {i}〜{i+len(batch)} 投入失敗: {e}")

    print()
    print(f"完了: {inserted}件をunit_price_masterに投入しました。")


if __name__ == "__main__":
    asyncio.run(main())

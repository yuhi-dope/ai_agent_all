"""デモデータ投入スクリプト — パイロット企業用。

Usage:
  cd shachotwo-app
  python scripts/seed_demo_data.py --company-id <UUID> --industry construction
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.supabase import get_service_client
from brain.genome.applicator import apply_template


# 建設業デモ: 現場・作業員・下請業者
DEMO_SITES = [
    {"name": "○○道路改良工事", "address": "東京都千代田区○○町1-1", "client_name": "国土交通省 関東地方整備局", "status": "active"},
    {"name": "△△河川護岸工事", "address": "埼玉県さいたま市△△区2-3", "client_name": "埼玉県 県土整備部", "status": "active"},
    {"name": "□□下水道管渠工事", "address": "神奈川県横浜市□□区4-5", "client_name": "横浜市 環境創造局", "status": "planning"},
]

DEMO_WORKERS = [
    {"last_name": "田中", "first_name": "太郎", "experience_years": 25, "blood_type": "A"},
    {"last_name": "鈴木", "first_name": "一郎", "experience_years": 15, "blood_type": "O"},
    {"last_name": "佐藤", "first_name": "健二", "experience_years": 10, "blood_type": "B"},
    {"last_name": "高橋", "first_name": "次郎", "experience_years": 8, "blood_type": "A"},
    {"last_name": "渡辺", "first_name": "三郎", "experience_years": 5, "blood_type": "AB"},
]

DEMO_QUALIFICATIONS = [
    {"qualification_type": "license", "qualification_name": "1級土木施工管理技士", "issuer": "国土交通省"},
    {"qualification_type": "license", "qualification_name": "2級土木施工管理技士", "issuer": "国土交通省"},
    {"qualification_type": "special_training", "qualification_name": "玉掛け技能講習", "issuer": "建設業労働災害防止協会"},
    {"qualification_type": "special_training", "qualification_name": "足場の組立て等作業主任者", "issuer": "建設業労働災害防止協会"},
    {"qualification_type": "license", "qualification_name": "普通自動車免許", "issuer": "公安委員会"},
]

DEMO_SUBCONTRACTORS = [
    {"name": "山田建設工業", "representative": "山田太郎", "specialties": ["土工", "コンクリート工"], "license_number": "東京都知事許可(般-99)第12345号"},
    {"name": "中村舗装", "representative": "中村次郎", "specialties": ["舗装工"], "license_number": "東京都知事許可(般-99)第23456号"},
    {"name": "小林電気工事", "representative": "小林三郎", "specialties": ["電気設備工事"], "license_number": "東京都知事許可(般-99)第34567号"},
]


async def seed_demo(company_id: str, industry: str) -> None:
    """デモデータを投入"""
    db = get_service_client()
    print(f"=== デモデータ投入開始: company_id={company_id}, industry={industry} ===")

    # 1. テンプレート適用
    print("1. テンプレート適用中...")
    try:
        result = await apply_template(template_id=industry, company_id=company_id)
        print(f"   → {result.items_created}件のナレッジを投入しました")
    except Exception as e:
        print(f"   → テンプレート適用スキップ（既に適用済み or エラー: {e}）")

    if industry != "construction":
        print("=== 完了（建設業以外はテンプレートのみ） ===")
        return

    # 2. 現場データ
    print("2. 現場データ投入中...")
    for site in DEMO_SITES:
        db.table("construction_sites").insert({
            "company_id": company_id,
            **site,
        }).execute()
    print(f"   → {len(DEMO_SITES)}件の現場を登録しました")

    # 3. 作業員データ
    print("3. 作業員データ投入中...")
    worker_ids = []
    for worker in DEMO_WORKERS:
        result = db.table("construction_workers").insert({
            "company_id": company_id,
            **worker,
        }).execute()
        if result.data:
            worker_ids.append(result.data[0]["id"])
    print(f"   → {len(DEMO_WORKERS)}名の作業員を登録しました")

    # 4. 資格データ
    print("4. 資格データ投入中...")
    qual_count = 0
    for i, wid in enumerate(worker_ids):
        # 最初の2人に多めの資格を付与
        quals_for_worker = DEMO_QUALIFICATIONS[:3] if i < 2 else DEMO_QUALIFICATIONS[3:]
        for q in quals_for_worker:
            db.table("worker_qualifications").insert({
                "worker_id": wid,
                "company_id": company_id,
                **q,
            }).execute()
            qual_count += 1
    print(f"   → {qual_count}件の資格を登録しました")

    # 5. 下請業者データ
    print("5. 下請業者データ投入中...")
    for sub in DEMO_SUBCONTRACTORS:
        db.table("subcontractors").insert({
            "company_id": company_id,
            **sub,
        }).execute()
    print(f"   → {len(DEMO_SUBCONTRACTORS)}社の下請業者を登録しました")

    print("=== デモデータ投入完了 ===")


def main():
    parser = argparse.ArgumentParser(description="デモデータ投入")
    parser.add_argument("--company-id", required=True, help="対象企業のUUID")
    parser.add_argument("--industry", default="construction", choices=["construction", "manufacturing", "dental"])
    args = parser.parse_args()

    asyncio.run(seed_demo(args.company_id, args.industry))


if __name__ == "__main__":
    main()

"""gBizINFO APIから製造業企業を一括取得し、leadsテーブルに流し込む。
セグメント分類（S/A/B/C）も同時に実行する。
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from workers.connector.gbizinfo import GBizInfoConnector, MANUFACTURING_KEYWORDS
from workers.connector.base import ConnectorConfig
from workers.bpo.sales.segmentation import classify_company, detect_sub_industry
from workers.micro.contact_extractor import batch_extract_contacts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# gBizINFO検索キーワード（サブ業種ごと）
# ---------------------------------------------------------------------------

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

# 1キーワードあたりの取得件数上限（APIレート制限を考慮）
_PER_KEYWORD_LIMIT = 50

# leadsテーブル source 値
_LEAD_SOURCE = "outbound"
_LEAD_SOURCE_DETAIL = "gbizinfo_bulk_load"


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_lead_row(
    company_id: str,
    gbiz_data: dict,
    segment_tier: str,
    sub_industry: str,
    contact_email: str = "",
    contact_form_url: str = "",
    contact_phone: str = "",
) -> dict:
    """gBizINFOデータ + セグメント情報からleads行dictを組み立てる。

    leads テーブルのスキーマ（021_sfa_crm_cs_tables.sql）に準拠する。
    追加フィールド（corporate_number, website_url 等）は source_detail / JSONB に格納する。
    """
    now = _now_iso()
    employee_count = gbiz_data.get("employee_count")
    if employee_count is not None:
        try:
            employee_count = int(employee_count)
        except (ValueError, TypeError):
            employee_count = None

    # スコアはセグメント階層から初期値を設定
    score_map = {"S": 80, "A": 60, "B": 40, "C": 20}
    initial_score = score_map.get(segment_tier, 20)

    return {
        "id": str(uuid4()),
        "company_id": company_id,
        "company_name": gbiz_data.get("name", ""),
        "contact_email": contact_email or None,
        "contact_phone": contact_phone or None,
        "industry": sub_industry,
        "employee_count": employee_count,
        "source": _LEAD_SOURCE,
        "source_detail": _LEAD_SOURCE_DETAIL,
        "score": initial_score,
        "score_reasons": [f"セグメント{segment_tier}（gBizINFO一括取得）"],
        "status": "new",
        # 拡張情報はsource_detailに補足情報として記録（スキーマ変更なし）
        # corporate_number / website_url / contact_form_url は別途 lead_activities に記録
        "created_at": now,
        "updated_at": now,
    }


async def _upsert_lead(
    supabase_client,
    lead_row: dict,
    corporate_number: str,
    company_id: str,
    website_url: str,
    contact_form_url: str,
) -> tuple[str, bool]:
    """leadsテーブルにupsertする。

    corporate_number が一致する既存リードがあれば UPDATE、なければ INSERT する。
    Returns:
        (lead_id, is_inserted): 処理したリードIDと新規挿入かどうか
    """
    # corporate_number で既存チェック（source_detail + company_name で代替判定）
    existing = (
        supabase_client
        .table("leads")
        .select("id, version")
        .eq("company_id", company_id)
        .eq("company_name", lead_row["company_name"])
        .eq("source_detail", _LEAD_SOURCE_DETAIL)
        .maybe_single()
        .execute()
    )

    is_inserted = False

    if existing.data:
        lead_id = existing.data["id"]
        current_version = existing.data.get("version", 1)
        update_data = {
            "contact_email": lead_row["contact_email"],
            "contact_phone": lead_row["contact_phone"],
            "employee_count": lead_row["employee_count"],
            "score": lead_row["score"],
            "score_reasons": lead_row["score_reasons"],
            "updated_at": _now_iso(),
            "version": current_version + 1,
        }
        supabase_client.table("leads").update(update_data).eq("id", lead_id).execute()
    else:
        result = supabase_client.table("leads").insert(lead_row).execute()
        lead_id = result.data[0]["id"]
        is_inserted = True

    # 企業サイトURL・フォームURLをlead_activitiesに記録（スキーマ拡張なし）
    if website_url or contact_form_url:
        activity_row = {
            "id": str(uuid4()),
            "company_id": company_id,
            "lead_id": lead_id,
            "activity_type": "research",
            "activity_data": {
                "corporate_number": corporate_number,
                "website_url": website_url,
                "contact_form_url": contact_form_url,
                "source": "gbizinfo_bulk_load",
            },
            "channel": "web",
            "created_at": _now_iso(),
        }
        try:
            supabase_client.table("lead_activities").insert(activity_row).execute()
        except Exception as e:
            logger.warning(f"lead_activities INSERT 失敗 (lead_id={lead_id}): {e}")

    return lead_id, is_inserted


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------

async def load_manufacturing_leads(
    api_token: str,
    company_id: str,
    target_sub_industries: Optional[list[str]] = None,
    extract_contacts: bool = True,
    dry_run: bool = False,
) -> dict:
    """製造業リードを一括ロードする。

    処理フロー:
    1. gBizINFO APIからサブ業種ごとにキーワード検索
    2. 重複排除（法人番号ベース）
    3. セグメント分類（S/A/B/C）
    4. HP → メール/フォーム自動抽出（extract_contacts=True 時）
    5. leadsテーブルにupsert（dry_run=False 時）

    Args:
        api_token:             gBizINFO APIトークン
        company_id:            自社のcompany_id（RLS対応）
        target_sub_industries: 対象サブ業種リスト（Noneなら全サブ業種）
        extract_contacts:      HP → メール/フォーム抽出を実行するか
        dry_run:               Trueなら DBへの書き込みを行わない

    Returns:
        {
            "total_fetched": int,
            "total_inserted": int,
            "total_updated": int,
            "by_sub_industry": {"金属加工": 42, ...},
            "by_priority": {"S": 5, "A": 15, ...},
            "contacts_extracted": int,
            "emails_found": int,
            "forms_found": int,
        }
    """
    from db.supabase import get_service_client

    config = ConnectorConfig(
        tool_name="gbizinfo",
        credentials={"api_token": api_token},
    )
    connector = GBizInfoConnector(config)
    supabase = get_service_client()

    # --- 対象サブ業種を確定 ---
    target_keys = target_sub_industries or list(SEARCH_KEYWORDS.keys())
    keywords_to_search: dict[str, list[str]] = {
        k: v for k, v in SEARCH_KEYWORDS.items() if k in target_keys
    }

    # --- Step 1: gBizINFO から一括取得 ---
    all_records: dict[str, dict] = {}  # corporate_number -> mapped_data
    by_sub_industry: dict[str, int] = {k: 0 for k in keywords_to_search}

    for sub_industry, keywords in keywords_to_search.items():
        logger.info(f"[manufacturing_lead_loader] {sub_industry} の検索開始 ({len(keywords)}キーワード)")
        for kw in keywords:
            try:
                records = await connector.read_records(
                    "search",
                    {"name": kw, "limit": _PER_KEYWORD_LIMIT},
                )
                for raw in records:
                    if not connector._is_manufacturing(raw):
                        continue
                    mapped = connector.map_to_company_data(raw)
                    corp_num = mapped.get("corporate_number", "")
                    if not corp_num:
                        continue
                    if corp_num not in all_records:
                        all_records[corp_num] = {**mapped, "_sub_industry_hint": sub_industry}
                        by_sub_industry[sub_industry] = by_sub_industry.get(sub_industry, 0) + 1
                # APIレート制限対策
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"[manufacturing_lead_loader] 検索エラー kw={kw}: {e}")

    total_fetched = len(all_records)
    logger.info(f"[manufacturing_lead_loader] 取得完了: 重複排除後 {total_fetched} 件")

    if total_fetched == 0:
        return {
            "total_fetched": 0,
            "total_inserted": 0,
            "total_updated": 0,
            "by_sub_industry": by_sub_industry,
            "by_priority": {"S": 0, "A": 0, "B": 0, "C": 0},
            "contacts_extracted": 0,
            "emails_found": 0,
            "forms_found": 0,
        }

    # --- Step 2: セグメント分類 ---
    records_list = list(all_records.values())
    by_priority: dict[str, int] = {"S": 0, "A": 0, "B": 0, "C": 0}
    classified: list[tuple[dict, str, str]] = []  # (mapped_data, tier, sub_industry)

    for rec in records_list:
        hint = rec.get("_sub_industry_hint", "")
        industry_text = " ".join(filter(None, [
            rec.get("industry", ""),
            rec.get("business_overview", ""),
            rec.get("name", ""),
            hint,
        ]))
        seg = classify_company(
            annual_revenue=None,
            operating_profit=None,
            employee_count=rec.get("employee_count"),
            industry_text=industry_text,
            capital_stock=rec.get("capital"),
        )
        tier = seg.priority_tier
        sub_ind = seg.sub_industry if seg.sub_industry != "その他製造" else (hint or "その他製造")
        by_priority[tier] = by_priority.get(tier, 0) + 1
        classified.append((rec, tier, sub_ind))

    # --- Step 3: HP → メール/フォーム抽出 ---
    contacts_extracted = 0
    emails_found = 0
    forms_found = 0
    contact_map: dict[str, tuple[str, str, str]] = {}  # corporate_number -> (email, form_url, phone)

    if extract_contacts:
        website_companies = [
            {"company_name": rec.get("name", ""), "website_url": rec.get("website_url", "")}
            for rec, _, _ in classified
            if rec.get("website_url")
        ]
        corp_nums_with_site = [
            rec.get("corporate_number", "")
            for rec, _, _ in classified
            if rec.get("website_url")
        ]

        if website_companies:
            logger.info(
                f"[manufacturing_lead_loader] HP連絡先抽出: {len(website_companies)} 件"
            )
            contact_results = await batch_extract_contacts(
                website_companies,
                concurrency=5,
                delay=1.0,
            )
            for corp_num, ci in zip(corp_nums_with_site, contact_results):
                email = ci.emails[0] if ci.emails else ""
                form_url = ci.contact_form_url or ""
                phone = ci.phone or ""
                contact_map[corp_num] = (email, form_url, phone)
                if not ci.error:
                    contacts_extracted += 1
                if email:
                    emails_found += 1
                if form_url:
                    forms_found += 1

    # --- Step 4: leadsテーブルにupsert ---
    total_inserted = 0
    total_updated = 0

    if not dry_run:
        for rec, tier, sub_ind in classified:
            corp_num = rec.get("corporate_number", "")
            email, form_url, phone = contact_map.get(corp_num, ("", "", ""))

            lead_row = _build_lead_row(
                company_id=company_id,
                gbiz_data=rec,
                segment_tier=tier,
                sub_industry=sub_ind,
                contact_email=email,
                contact_form_url=form_url,
                contact_phone=phone,
            )

            try:
                _, is_inserted = await _upsert_lead(
                    supabase_client=supabase,
                    lead_row=lead_row,
                    corporate_number=corp_num,
                    company_id=company_id,
                    website_url=rec.get("website_url", ""),
                    contact_form_url=form_url,
                )
                if is_inserted:
                    total_inserted += 1
                else:
                    total_updated += 1
            except Exception as e:
                logger.error(
                    f"[manufacturing_lead_loader] upsert失敗 "
                    f"corp_num={corp_num}, name={rec.get('name')}: {e}"
                )
    else:
        logger.info(
            f"[manufacturing_lead_loader] dry_run=True のため DB書き込みをスキップ "
            f"(対象: {total_fetched} 件)"
        )
        total_inserted = total_fetched  # dry_run では想定件数を返す

    result = {
        "total_fetched": total_fetched,
        "total_inserted": total_inserted,
        "total_updated": total_updated,
        "by_sub_industry": by_sub_industry,
        "by_priority": by_priority,
        "contacts_extracted": contacts_extracted,
        "emails_found": emails_found,
        "forms_found": forms_found,
    }
    logger.info(f"[manufacturing_lead_loader] 完了: {result}")
    return result

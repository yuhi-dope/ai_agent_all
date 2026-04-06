"""kintone 製造業リードアプリからレコードを取得し leads に取り込む。

先頭 N 件（既定 10 件）でマッピング・API を検証し、問題なければ $id ベースで全件取得する。
b_10 / 実装メモに準拠。共通ループは kintone_lead_import_runner。
"""
from __future__ import annotations

from typing import Any, Optional

from workers.bpo.sales.kintone_credentials import resolve_kintone_credentials
from workers.bpo.sales.kintone_lead_import_runner import (
    build_page_query,
    flat_from_record,
    record_numeric_id,
    run_kintone_lead_import,
)

# kintone_manufacturing_leads.json と同一のフィールドコード想定（マッピングで上書き可）
_DEFAULT_FIELD_CODES = (
    "name",
    "corporate_number",
    "website_url",
    "phone",
    "fax",
    "employee_count",
    "capital_stock",
    "annual_revenue",
    "operating_profit",
    "prefecture",
    "address",
    "representative",
    "sub_industry",
    "business_description",
    "tsr_code",
    "profit_segment",
    "priority_tier",
    "source",
    # TSR fields
    "tsr_category_large",
    "tsr_category_medium",
    "tsr_category_small",
    "tsr_category_detail",
    "tsr_business_items",
    "tsr_suppliers",
    "tsr_customers",
    "tsr_representative",
    "representative_phone",
    "tsr_revenue_latest",
    "tsr_profit_latest",
)

# kintone フィールドコード（日本語）→ canonical キーのデフォルトマッピング
_DEFAULT_FIELD_MAPPINGS: dict[str, str] = {
    "tsr_category_large": "大分類_TSR",
    "tsr_category_medium": "中分類_TSR",
    "tsr_category_small": "小分類_TSR",
    "tsr_category_detail": "細分類_TSR",
    "tsr_business_items": "営業種目_TSR",
    "tsr_suppliers": "仕入先_TSR",
    "tsr_customers": "販売先_TSR",
    "tsr_representative": "代表者_TSR",
    "representative_phone": "Master_DaihyouBangou",
    "tsr_revenue_latest": "売上_直近期_TSR",
    "tsr_profit_latest": "純利益_直近期_TSR",
}


def _as_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip().replace(",", "")
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    return None


def _as_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        t = v.strip()
        return t if t else None
    return str(v).strip() or None


def kintone_record_to_flat(
    rec: dict,
    field_codes: tuple[str, ...] = _DEFAULT_FIELD_CODES,
    field_mappings: dict[str, str] | None = None,
) -> dict[str, Any]:
    return flat_from_record(rec, field_codes, field_mappings)


def _infer_city(prefecture: Optional[str], address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    if prefecture and address.startswith(prefecture):
        rest = address[len(prefecture) :].strip()
        return rest or None
    return address


def map_flat_to_lead_row(
    flat: dict[str, Any],
    *,
    company_id: str,
    app_id: str,
) -> Optional[dict[str, Any]]:
    corp = _as_str(flat.get("corporate_number"))
    name = _as_str(flat.get("name"))
    if not corp or not name:
        return None

    industry_text = _as_str(flat.get("business_description")) or _as_str(flat.get("sub_industry")) or ""
    from workers.bpo.sales.segmentation import classify_company

    seg = classify_company(
        annual_revenue=_as_int(flat.get("annual_revenue")),
        operating_profit=_as_int(flat.get("operating_profit")),
        employee_count=_as_int(flat.get("employee_count")),
        industry_text=industry_text,
        capital_stock=_as_int(flat.get("capital_stock")),
    )

    pref = _as_str(flat.get("prefecture"))
    addr = _as_str(flat.get("address"))
    src_detail = _as_str(flat.get("source")) or f"kintone_app{app_id}"

    row: dict[str, Any] = {
        "company_id": company_id,
        "company_name": name,
        "contact_phone": _as_str(flat.get("phone")),
        "contact_email": None,
        "industry": "manufacturing",
        "employee_count": _as_int(flat.get("employee_count")),
        "source": "kintone",
        "source_detail": src_detail[:500] if src_detail else f"kintone_app{app_id}",
        "status": "new",
        "corporate_number": corp,
        "capital_stock": _as_int(flat.get("capital_stock")),
        "annual_revenue": _as_int(flat.get("annual_revenue")),
        "operating_profit": _as_int(flat.get("operating_profit")),
        "sub_industry": _as_str(flat.get("sub_industry")) or seg.sub_industry,
        "prefecture": pref,
        "city": _infer_city(pref, addr),
        "website_url": _as_str(flat.get("website_url")),
        "representative": _as_str(flat.get("representative")),
        "business_overview": _as_str(flat.get("business_description")),
        "revenue_segment": seg.revenue_segment,
        "profit_segment": _as_str(flat.get("profit_segment")) or seg.profit_segment,
        "priority_tier": _as_str(flat.get("priority_tier")) or seg.priority_tier,
        # TSR fields
        "tsr_category_large": _as_str(flat.get("tsr_category_large")),
        "tsr_category_medium": _as_str(flat.get("tsr_category_medium")),
        "tsr_category_small": _as_str(flat.get("tsr_category_small")),
        "tsr_category_detail": _as_str(flat.get("tsr_category_detail")),
        "tsr_business_items": _as_str(flat.get("tsr_business_items")),
        "tsr_suppliers": _as_str(flat.get("tsr_suppliers")),
        "tsr_customers": _as_str(flat.get("tsr_customers")),
        "tsr_representative": _as_str(flat.get("tsr_representative")),
        "representative_phone": _as_str(flat.get("representative_phone")),
        "tsr_revenue_latest": _as_int(flat.get("tsr_revenue_latest")),
        "tsr_profit_latest": _as_int(flat.get("tsr_profit_latest")),
    }
    return row


def _map_mfg_flat(flat: dict[str, Any], company_id: str, app_id: str) -> dict[str, Any] | None:
    return map_flat_to_lead_row(flat, company_id=company_id, app_id=app_id)


async def import_manufacturing_leads_from_kintone(
    *,
    subdomain: str,
    api_token: str,
    app_id: str,
    company_id: str,
    base_query: str = "",
    probe_size: int = 10,
    dry_run: bool = False,
    field_mappings: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged_mappings = {**_DEFAULT_FIELD_MAPPINGS, **(field_mappings or {})}
    return await run_kintone_lead_import(
        subdomain=subdomain,
        api_token=api_token,
        app_id=app_id,
        company_id=company_id,
        base_query=base_query,
        probe_size=probe_size,
        dry_run=dry_run,
        canonical_field_keys=_DEFAULT_FIELD_CODES,
        field_mappings=merged_mappings,
        map_flat_to_row=_map_mfg_flat,
        log_prefix="kintone_mfg_import",
    )


__all__ = [
    "resolve_kintone_credentials",
    "import_manufacturing_leads_from_kintone",
    "map_flat_to_lead_row",
    "kintone_record_to_flat",
    "build_page_query",
    "record_numeric_id",
    "_DEFAULT_FIELD_CODES",
    "_DEFAULT_FIELD_MAPPINGS",
]

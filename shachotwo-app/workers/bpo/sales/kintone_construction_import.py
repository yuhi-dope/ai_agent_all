"""kintone 建設業リード → leads（b_10 §3-7）。"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from workers.bpo.sales.kintone_credentials import resolve_kintone_credentials
from workers.bpo.sales.kintone_lead_import_runner import run_kintone_lead_import
from workers.bpo.sales.segmentation_construction import classify_construction_lead

# b_10 既定フィールドコード（kintone 側コード。マッピングで上書き可）
_DEFAULT_CONSTRUCTION_FIELD_CODES = (
    "name",
    "corporate_number",
    "prefecture",
    "address",
    "phone",
    "employee_count",
    "annual_revenue",
    "operating_profit",
    "website_url",
    "representative",
    "main_work_type",
    "contractor_license",
    "permit_expiry",
)


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


def _parse_date(v: Any) -> Optional[str]:
    """leads.permit_expiry_date 用 ISO 日付文字列。"""
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip().split("T")[0].replace("/", "-")
    parts = s.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        y, mo, d = int(parts[0]), int(parts[1]), int(parts[2])
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    try:
        from datetime import datetime as dt

        return dt.fromisoformat(str(v).strip().replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _infer_city(prefecture: Optional[str], address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    if prefecture and address.startswith(prefecture):
        rest = address[len(prefecture) :].strip()
        return rest or None
    return address


def map_flat_to_construction_lead_row(
    flat: dict[str, Any],
    *,
    company_id: str,
    app_id: str,
) -> Optional[dict[str, Any]]:
    corp = _as_str(flat.get("corporate_number"))
    name = _as_str(flat.get("name"))
    if not corp or not name:
        return None

    mwt = _as_str(flat.get("main_work_type")) or ""
    seg = classify_construction_lead(
        annual_revenue=_as_int(flat.get("annual_revenue")),
        operating_profit=_as_int(flat.get("operating_profit")),
        employee_count=_as_int(flat.get("employee_count")),
        main_work_type=mwt,
    )
    pref = _as_str(flat.get("prefecture"))
    addr = _as_str(flat.get("address"))

    row: dict[str, Any] = {
        "company_id": company_id,
        "company_name": name,
        "contact_phone": _as_str(flat.get("phone")),
        "contact_email": None,
        "industry": "construction",
        "employee_count": _as_int(flat.get("employee_count")),
        "source": "kintone",
        "source_detail": f"kintone_app{app_id}",
        "status": "new",
        "corporate_number": corp,
        "annual_revenue": _as_int(flat.get("annual_revenue")),
        "operating_profit": _as_int(flat.get("operating_profit")),
        "sub_industry": seg["sub_industry"],
        "prefecture": pref,
        "city": _infer_city(pref, addr),
        "website_url": _as_str(flat.get("website_url")),
        "representative": _as_str(flat.get("representative")),
        "business_overview": mwt,
        "revenue_segment": seg["revenue_segment"],
        "profit_segment": seg["profit_segment"],
        "priority_tier": seg["priority_tier"],
        "contractor_license_number": _as_str(flat.get("contractor_license")),
        "permit_expiry_date": _parse_date(flat.get("permit_expiry")),
    }
    return row


def _map_const_flat(flat: dict[str, Any], company_id: str, app_id: str) -> dict[str, Any] | None:
    return map_flat_to_construction_lead_row(flat, company_id=company_id, app_id=app_id)


async def import_construction_leads_from_kintone(
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
    return await run_kintone_lead_import(
        subdomain=subdomain,
        api_token=api_token,
        app_id=app_id,
        company_id=company_id,
        base_query=base_query,
        probe_size=probe_size,
        dry_run=dry_run,
        canonical_field_keys=_DEFAULT_CONSTRUCTION_FIELD_CODES,
        field_mappings=field_mappings,
        map_flat_to_row=_map_const_flat,
        log_prefix="kintone_const_import",
    )

"""kintone → leads 取り込みの共通ループ（製造・建設で再利用）。

b_10 要件: $id ページング・プローブ・field_mappings 対応。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Optional

from workers.connector.base import ConnectorConfig
from workers.connector.kintone import KintoneConnector, KINTONE_MAX_LIMIT

logger = logging.getLogger(__name__)


def _cell_value(cell: Any) -> Any:
    if cell is None or not isinstance(cell, dict):
        return None
    return cell.get("value")


def record_numeric_id(rec: dict) -> int:
    raw = _cell_value(rec.get("$id"))
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def build_page_query(base_filter: str, min_id: int) -> str:
    parts: list[str] = []
    if base_filter.strip():
        parts.append(f"({base_filter.strip()})")
    if min_id > 0:
        parts.append(f"$id > {min_id}")
    cond = " and ".join(parts)
    if cond:
        return f"{cond} order by $id asc"
    return "order by $id asc"


def flat_from_record(
    rec: dict,
    canonical_field_keys: tuple[str, ...],
    field_mappings: dict[str, str] | None,
) -> dict[str, Any]:
    """field_mappings: canonical キー → kintone フィールドコード（b_10 §3-3）。"""
    m = field_mappings or {}
    out: dict[str, Any] = {}
    for key in canonical_field_keys:
        kintone_code = m.get(key) or key
        out[key] = _cell_value(rec.get(kintone_code))
    return out


async def run_kintone_lead_import(
    *,
    subdomain: str,
    api_token: str,
    app_id: str,
    company_id: str,
    base_query: str,
    probe_size: int,
    dry_run: bool,
    canonical_field_keys: tuple[str, ...],
    field_mappings: dict[str, str] | None,
    map_flat_to_row: Callable[[dict[str, Any], str, str], dict[str, Any] | None],
    log_prefix: str = "kintone_import",
) -> dict[str, Any]:
    cfg = ConnectorConfig(
        tool_name="kintone",
        credentials={"subdomain": subdomain, "api_token": api_token},
    )
    conn = KintoneConnector(cfg)
    from db.supabase import get_service_client

    db = get_service_client()
    probe_size = max(1, min(probe_size, KINTONE_MAX_LIMIT))
    last_id = 0
    total_received = 0
    total_upsert_ok = 0
    total_skipped = 0
    probe_phase = True
    probe_ok = False

    while True:
        batch_limit = probe_size if probe_phase else KINTONE_MAX_LIMIT
        q = build_page_query(base_query, last_id)
        try:
            batch = await conn.read_records_page(app_id, query=q, limit=batch_limit)
        except Exception as e:
            logger.error("[%s] fetch failed query=%s: %s", log_prefix, q, e)
            raise

        if not batch:
            if probe_phase and total_received == 0:
                logger.info("[%s] no records in app %s", log_prefix, app_id)
            break

        for rec in batch:
            total_received += 1
            rid = record_numeric_id(rec)
            if rid > last_id:
                last_id = rid

            flat = flat_from_record(rec, canonical_field_keys, field_mappings)
            row = map_flat_to_row(flat, company_id, app_id)
            if not row:
                total_skipped += 1
                continue

            if dry_run:
                total_upsert_ok += 1
                continue

            try:
                db.table("leads").upsert(
                    row,
                    on_conflict="company_id,corporate_number",
                ).execute()
                total_upsert_ok += 1
            except Exception as upsert_err:
                logger.warning(
                    "[%s] upsert skipped corp=%s: %s",
                    log_prefix,
                    row.get("corporate_number"),
                    upsert_err,
                )
                total_skipped += 1

        if probe_phase:
            if total_received > 0 and total_upsert_ok == 0 and not dry_run:
                raise RuntimeError(
                    "プローブ段階: レコードは取得できましたが 1 件も upsert できませんでした。"
                    "法人番号・企業名フィールドコードを確認してください。"
                )
            probe_ok = True
            probe_phase = False
            await asyncio.sleep(0.15)
            continue

        if len(batch) < batch_limit:
            break
        await asyncio.sleep(0.15)

    return {
        "probe_ok": probe_ok,
        "total_received": total_received,
        "total_upsert_ok": total_upsert_ok,
        "total_skipped": total_skipped,
        "dry_run": dry_run,
    }

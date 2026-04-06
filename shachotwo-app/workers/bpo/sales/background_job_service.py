"""background_jobs テーブルの更新（サービスロール・RLS バイパス）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job_row(
    db: Any,
    *,
    job_id: str,
    company_id: str,
    job_type: str,
    payload: dict[str, Any],
) -> None:
    db.table("background_jobs").insert({
        "id": job_id,
        "company_id": company_id,
        "job_type": job_type,
        "status": "queued",
        "payload": payload,
    }).execute()


def mark_job_running(db: Any, job_id: str) -> None:
    db.table("background_jobs").update({
        "status": "running",
        "started_at": _now_iso(),
    }).eq("id", job_id).execute()


def mark_job_completed(db: Any, job_id: str, result: dict[str, Any]) -> None:
    db.table("background_jobs").update({
        "status": "completed",
        "result": result,
        "completed_at": _now_iso(),
        "error_message": None,
    }).eq("id", job_id).execute()


def mark_job_failed(db: Any, job_id: str, message: str) -> None:
    db.table("background_jobs").update({
        "status": "failed",
        "error_message": (message or "")[:5000],
        "completed_at": _now_iso(),
    }).eq("id", job_id).execute()


def fetch_field_mappings_for_app(
    db: Any,
    company_id: str,
    app_id: str,
) -> dict[str, str] | None:
    res = (
        db.table("kintone_field_mappings")
        .select("field_mappings")
        .eq("company_id", company_id)
        .eq("app_id", app_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    raw = res.data[0].get("field_mappings") or {}
    if not isinstance(raw, dict) or not raw:
        return None
    return {str(k): str(v) for k, v in raw.items()}

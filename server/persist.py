"""
run 終了後に Supabase に runs / features を保存する。
SUPABASE_URL または SUPABASE_SERVICE_KEY が未設定の場合は何もしない。
"""

import json
import os
from pathlib import Path
from typing import Optional


def _get_client():
    """Supabase クライアントを返す。未設定時は None。"""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (
        os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    ).strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


def _extract_purpose_snippet(spec_markdown: str, max_chars: int = 400) -> str:
    """spec_markdown から目的の抜粋を取得。"""
    spec = (spec_markdown or "").strip()
    if not spec:
        return ""
    if "## 目的" in spec:
        start = spec.find("## 目的")
        end = spec.find("\n## ", start + 1) if start >= 0 else -1
        segment = spec[start : end] if end > start else spec[start : start + max_chars]
        return segment.strip()[:max_chars]
    return spec[:max_chars]


def persist_run(
    workspace_root: Path,
    output_subdir: str,
    result: dict,
) -> None:
    """
    result と output 配下の spec から runs / features を Supabase に 1 件ずつ insert する。
    環境変数未設定時は何もしない。
    """
    client = _get_client()
    if not client:
        return
    run_id = result.get("run_id") or ""
    if not run_id:
        return
    spec_markdown = result.get("spec_markdown") or ""
    spec_purpose = _extract_purpose_snippet(spec_markdown)
    requirement = (result.get("user_requirement") or "")[:500]
    status = result.get("status") or ""
    retry_count = result.get("retry_count") or 0
    pr_url = result.get("pr_url") or ""

    genre = (result.get("genre") or "")[:100]
    genre_override_reason = (result.get("genre_override_reason") or "")[:500]

    try:
        client.table("runs").insert(
            {
                "run_id": run_id,
                "requirement_summary": requirement or None,
                "spec_purpose": spec_purpose or None,
                "spec_markdown": spec_markdown or None,
                "status": status,
                "retry_count": retry_count,
                "pr_url": pr_url or None,
                "output_subdir": output_subdir or None,
                "genre": genre or None,
                "genre_override_reason": genre_override_reason or None,
                "notion_page_id": (result.get("notion_page_id") or "") or None,
            }
        ).execute()
    except Exception:
        pass

    persist_features(run_id, result)


def persist_features(run_id: str, result: dict) -> None:
    """features テーブルに 1 件 insert する。"""
    client = _get_client()
    if not client or not run_id:
        return
    spec_markdown = result.get("spec_markdown") or ""
    spec_purpose = _extract_purpose_snippet(spec_markdown)
    requirement = (result.get("user_requirement") or "")[:500]
    generated_code = result.get("generated_code") or {}
    file_list = list(generated_code.keys())
    summary = (spec_purpose or requirement or "(要約なし)")[:500]
    try:
        client.table("features").insert(
            {
                "run_id": run_id,
                "summary": summary,
                "file_list": file_list,
            }
        ).execute()
    except Exception:
        pass


def persist_spec_snapshot(result: dict) -> None:
    """Phase 1 完了後に state_snapshot を JSONB で保存する（spec_review 用）。"""
    client = _get_client()
    if not client:
        return
    run_id = result.get("run_id") or ""
    if not run_id:
        return
    spec_markdown = result.get("spec_markdown") or ""
    spec_purpose = _extract_purpose_snippet(spec_markdown)
    requirement = (result.get("user_requirement") or "")[:500]
    genre = (result.get("genre") or "")[:100]
    genre_override_reason = (result.get("genre_override_reason") or "")[:500]
    notion_page_id = result.get("notion_page_id") or ""

    snapshot = {
        "user_requirement": result.get("user_requirement") or "",
        "spec_markdown": spec_markdown,
        "generated_code": {},
        "error_logs": result.get("error_logs") or [],
        "retry_count": 0,
        "status": "spec_review",
        "fix_instruction": "",
        "last_error_signature": "",
        "pr_url": "",
        "workspace_root": result.get("workspace_root") or ".",
        "rules_dir": result.get("rules_dir") or "rules",
        "run_id": run_id,
        "output_subdir": result.get("output_subdir") or "",
        "output_rules_improvement": result.get("output_rules_improvement") or False,
        "genre": result.get("genre") or "",
        "genre_subcategory": result.get("genre_subcategory") or "",
        "genre_override_reason": result.get("genre_override_reason") or "",
        "total_input_tokens": result.get("total_input_tokens") or 0,
        "total_output_tokens": result.get("total_output_tokens") or 0,
        "notion_page_id": notion_page_id,
    }

    try:
        client.table("runs").insert(
            {
                "run_id": run_id,
                "requirement_summary": requirement or None,
                "spec_purpose": spec_purpose or None,
                "spec_markdown": spec_markdown or None,
                "status": "spec_review",
                "retry_count": 0,
                "pr_url": None,
                "output_subdir": result.get("output_subdir") or None,
                "genre": genre or None,
                "genre_override_reason": genre_override_reason or None,
                "notion_page_id": notion_page_id or None,
                "state_snapshot": json.dumps(snapshot, ensure_ascii=False),
            }
        ).execute()
    except Exception:
        pass


def load_state_snapshot(run_id: str) -> dict | None:
    """run_id から state_snapshot を取得して返す。status=spec_review のもののみ。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = (
            client.table("runs")
            .select("state_snapshot, status")
            .eq("run_id", run_id)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return None
        row = rows[0]
        if row.get("status") != "spec_review":
            return None
        snapshot = row.get("state_snapshot")
        if isinstance(snapshot, str):
            return json.loads(snapshot)
        return snapshot
    except Exception:
        return None


def update_run_status(run_id: str, updates: dict) -> None:
    """run_id で runs テーブルの特定カラムを更新する。"""
    client = _get_client()
    if not client:
        return
    try:
        client.table("runs").update(updates).eq("run_id", run_id).execute()
    except Exception:
        pass


def get_run_by_id(run_id: str) -> dict | None:
    """run_id で 1 件取得する。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = client.table("runs").select("*").eq("run_id", run_id).limit(1).execute()
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_runs(limit: int = 50) -> list:
    """Supabase から runs を created_at 降順で取得。未設定時は空リスト。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("runs")
            .select("run_id, requirement_summary, spec_purpose, spec_markdown, status, retry_count, pr_url, output_subdir, genre, genre_override_reason, notion_page_id, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def persist_audit_logs(run_id: str, audit_records: list[dict]) -> None:
    """Insert sandbox audit log records into Supabase audit_logs table."""
    client = _get_client()
    if not client or not run_id or not audit_records:
        return
    try:
        rows = []
        for record in audit_records:
            rows.append(
                {
                    "run_id": run_id,
                    "tool_name": record.get("tool", "unknown"),
                    "arguments": record.get("arguments"),
                    "result_summary": record.get("result_summary"),
                    "source": "sandbox",
                    "logged_at": record.get("timestamp", "1970-01-01T00:00:00Z"),
                }
            )
        if rows:
            client.table("audit_logs").insert(rows).execute()
    except Exception:
        pass


def get_features(run_id: Optional[str] = None, limit: int = 100) -> list:
    """Supabase から features を取得。run_id 指定時はその run のみ。未設定時は空リスト。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = client.table("features").select("*").order("created_at", desc=True)
        if run_id:
            q = q.eq("run_id", run_id)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []

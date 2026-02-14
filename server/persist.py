"""
run 終了後に Supabase に runs / features を保存する。
SUPABASE_URL または SUPABASE_SERVICE_KEY が未設定の場合は何もしない。
"""

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
    generated_code = result.get("generated_code") or {}
    file_list = list(generated_code.keys())

    try:
        client.table("runs").insert(
            {
                "run_id": run_id,
                "requirement_summary": requirement or None,
                "spec_purpose": spec_purpose or None,
                "status": status,
                "retry_count": retry_count,
                "pr_url": pr_url or None,
                "output_subdir": output_subdir or None,
            }
        ).execute()
    except Exception:
        pass

    summary = (spec_purpose or requirement or "（要約なし）")[:500]
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


def get_runs(limit: int = 50) -> list:
    """Supabase から runs を created_at 降順で取得。未設定時は空リスト。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("runs")
            .select("run_id, requirement_summary, spec_purpose, status, pr_url, output_subdir, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


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

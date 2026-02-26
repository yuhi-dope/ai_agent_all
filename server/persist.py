"""
run 終了後に Supabase に runs / features を保存する。
SUPABASE_URL または SUPABASE_SERVICE_KEY が未設定の場合は何もしない。
"""

import json
from pathlib import Path
from typing import Optional


def _get_client():
    """Supabase クライアントを返す（シングルトン）。"""
    from server._supabase import get_client

    return get_client()


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
    company_id: Optional[str] = None,
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
    genre = (result.get("genre") or "")[:100]
    genre_override_reason = (result.get("genre_override_reason") or "")[:500]

    row = {
        "run_id": run_id,
        "requirement_summary": requirement or None,
        "spec_purpose": spec_purpose or None,
        "spec_markdown": spec_markdown or None,
        "status": status,
        "retry_count": retry_count,
        "output_subdir": output_subdir or None,
        "genre": genre or None,
        "genre_override_reason": genre_override_reason or None,
        "notion_page_id": (result.get("notion_page_id") or "") or None,
    }
    if company_id:
        row["company_id"] = company_id

    try:
        client.table("runs").insert(row).execute()
    except Exception:
        pass

    persist_features(run_id, result, company_id=company_id)


def persist_features(run_id: str, result: dict, company_id: Optional[str] = None) -> None:
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
    row = {
        "run_id": run_id,
        "summary": summary,
        "file_list": file_list,
    }
    if company_id:
        row["company_id"] = company_id
    try:
        client.table("features").insert(row).execute()
    except Exception:
        pass


def persist_spec_snapshot(result: dict, company_id: Optional[str] = None) -> None:
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

    row = {
        "run_id": run_id,
        "requirement_summary": requirement or None,
        "spec_purpose": spec_purpose or None,
        "spec_markdown": spec_markdown or None,
        "status": "spec_review",
        "retry_count": 0,
        "output_subdir": result.get("output_subdir") or None,
        "genre": genre or None,
        "genre_override_reason": genre_override_reason or None,
        "notion_page_id": notion_page_id or None,
        "state_snapshot": json.dumps(snapshot, ensure_ascii=False),
    }
    if company_id:
        row["company_id"] = company_id

    try:
        client.table("runs").insert(row).execute()
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


def get_runs(limit: int = 50, company_id: Optional[str] = None) -> list:
    """Supabase から runs を created_at 降順で取得。company_id でフィルタ可。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("runs")
            .select("run_id, requirement_summary, spec_purpose, spec_markdown, status, retry_count, output_subdir, genre, genre_override_reason, notion_page_id, created_at")
            .order("created_at", desc=True)
        )
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def persist_audit_logs(run_id: str, audit_records: list[dict], source: str = "sandbox") -> None:
    """Insert audit log records into Supabase audit_logs table.

    source="sandbox" の場合は従来の sandbox MCP ツール実行ログ。
    source="saas" の場合は SaaS 操作の監査ログ（company_id, saas_name 等を含む）。
    """
    client = _get_client()
    if not client or not run_id or not audit_records:
        return
    try:
        rows = []
        for record in audit_records:
            row = {
                "run_id": run_id,
                "tool_name": record.get("tool", "unknown"),
                "arguments": record.get("arguments"),
                "result_summary": record.get("result_summary"),
                "source": source,
                "logged_at": record.get("timestamp", "1970-01-01T00:00:00Z"),
            }
            # SaaS 監査ログの追加フィールド
            if record.get("company_id"):
                row["company_id"] = record["company_id"]
            if record.get("saas_name"):
                row["saas_name"] = record["saas_name"]
            if record.get("genre"):
                row["genre"] = record["genre"]
            if record.get("connection_id"):
                row["connection_id"] = record["connection_id"]
            rows.append(row)
        if rows:
            client.table("audit_logs").insert(rows).execute()
    except Exception:
        pass


def get_runs_detail(limit: int = 50) -> list:
    """runs テーブルから state_snapshot 含む全カラムを取得（admin 用）。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("runs")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_audit_logs(run_id: Optional[str] = None, limit: int = 200) -> list:
    """audit_logs テーブルを取得。run_id 指定時はその run のみ。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = client.table("audit_logs").select("*").order("logged_at", desc=True)
        if run_id:
            q = q.eq("run_id", run_id)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_saas_audit_logs(
    company_id: str,
    saas_name: Optional[str] = None,
    connection_id: Optional[str] = None,
    limit: int = 200,
) -> list:
    """SaaS操作の監査ログを取得する。company_id 必須。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("audit_logs")
            .select("*")
            .eq("source", "saas")
            .eq("company_id", company_id)
            .order("logged_at", desc=True)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        if connection_id:
            q = q.eq("connection_id", connection_id)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_oauth_status() -> list:
    """oauth_tokens テーブルから provider, tenant_id, expires_at, updated_at を取得。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("oauth_tokens")
            .select("provider, tenant_id, expires_at, scopes, updated_at")
            .order("provider")
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_rule_changes(status: Optional[str] = None, limit: int = 100) -> list:
    """rule_changes テーブルを取得。status でフィルタ可。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = client.table("rule_changes").select("*").order("created_at", desc=True)
        if status:
            q = q.eq("status", status)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_rule_change_by_id(change_id: str) -> dict | None:
    """rule_changes から 1 件取得。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = (
            client.table("rule_changes")
            .select("*")
            .eq("id", change_id)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def update_rule_change_status(
    change_id: str, status: str, reviewed_by: Optional[str] = None
) -> bool:
    """rule_changes のステータスを更新する。"""
    client = _get_client()
    if not client:
        return False
    try:
        from datetime import datetime, timezone

        updates: dict = {"status": status}
        if reviewed_by:
            updates["reviewed_by"] = reviewed_by
        updates["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        client.table("rule_changes").update(updates).eq("id", change_id).execute()
        return True
    except Exception:
        return False


def get_features(run_id: Optional[str] = None, company_id: Optional[str] = None, limit: int = 100) -> list:
    """Supabase から features を取得。run_id / company_id でフィルタ可。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = client.table("features").select("*").order("created_at", desc=True)
        if run_id:
            q = q.eq("run_id", run_id)
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []

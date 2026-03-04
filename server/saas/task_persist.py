"""SaaS BPO タスクの永続化層.

saas_tasks テーブルの CRUD + 失敗パターン検索。
persist.py と同じパターン（Supabase クライアント、エラー握りつぶし）に準拠。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional


def _get_client():
    """Supabase クライアントを返す（シングルトン）。"""
    from server._supabase import get_client

    return get_client()


def normalize_failure_reason(reason: str) -> str:
    """failure_reason から一意ID・タイムスタンプ等を除去し、パターン集約可能にする。

    例:
      '{"code":"CB_VA01","id":"wfM68zIHCk","message":"入力内容が正しくありません。"}'
      → 'CB_VA01: 入力内容が正しくありません。'

      'Error 403: Forbidden (request_id: abc123-def456)'
      → 'Error 403: Forbidden'
    """
    if not reason:
        return reason

    # kintone JSON エラーを正規化: {"code":"XXX","id":"...","message":"..."}
    json_match = re.search(r'\{[^{}]*"code"\s*:\s*"([^"]+)"[^{}]*"message"\s*:\s*"([^"]*)"[^{}]*\}', reason)
    if json_match:
        code = json_match.group(1)
        message = json_match.group(2)
        return f"{code}: {message}"

    # JSON 文字列の場合パースして正規化
    if reason.strip().startswith("{"):
        try:
            data = json.loads(reason)
            code = data.get("code", "")
            message = data.get("message", "")
            if code:
                return f"{code}: {message}" if message else code
        except (json.JSONDecodeError, TypeError):
            pass

    # 一意ID パターンを除去 (UUID, hex ID, request_id 等)
    normalized = reason
    # UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    normalized = re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<ID>', normalized)
    # 長い hex 文字列 (10文字以上)
    normalized = re.sub(r'[0-9a-fA-F]{10,}', '<ID>', normalized)
    # request_id / id パラメータ
    normalized = re.sub(r'(?:request_id|id)\s*[:=]\s*\S+', '', normalized)
    # 括弧内の ID 参照を除去
    normalized = re.sub(r'\(\s*\)', '', normalized)
    # 余分な空白を正規化
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def _generate_task_id() -> str:
    """一意なタスクIDを生成。"""
    return f"task_{uuid.uuid4().hex[:12]}"


def create_task(
    company_id: str,
    connection_id: str,
    task_description: str,
    saas_name: str,
    genre: str = "",
    dry_run: bool = False,
    task_id: str | None = None,
) -> dict | None:
    """saas_tasks テーブルに新規タスクを INSERT する。"""
    client = _get_client()
    if not client:
        return None
    tid = task_id or _generate_task_id()
    row = {
        "company_id": company_id,
        "connection_id": connection_id,
        "task_id": tid,
        "task_description": task_description,
        "saas_name": saas_name,
        "genre": genre or None,
        "status": "planning",
        "dry_run": dry_run,
    }
    try:
        r = client.table("saas_tasks").insert(row).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None


def get_tasks(
    company_id: str,
    status: Optional[str] = None,
    limit: int = 50,
) -> list:
    """company_id に紐づくタスク一覧を取得。status でフィルタ可。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("saas_tasks")
            .select("task_id, task_description, saas_name, genre, status, "
                    "plan_markdown, operation_count, result_summary, report_markdown, "
                    "failure_reason, failure_category, dry_run, "
                    "plan_confidence, plan_warnings, "
                    "created_at, approved_at, completed_at, duration_ms")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
        )
        if status:
            q = q.eq("status", status)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_task(task_id: str, company_id: Optional[str] = None) -> dict | None:
    """task_id でタスク詳細を取得。"""
    client = _get_client()
    if not client:
        return None
    try:
        q = (
            client.table("saas_tasks")
            .select("*")
            .eq("task_id", task_id)
        )
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(1).execute()
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def update_task(task_id: str, updates: dict) -> bool:
    """task_id でタスクを更新。"""
    client = _get_client()
    if not client:
        return False
    try:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        client.table("saas_tasks").update(updates).eq("task_id", task_id).execute()
        return True
    except Exception:
        return False


def delete_task(task_id: str) -> bool:
    """タスクを削除する。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("saas_tasks").delete().eq("task_id", task_id).execute()
        return True
    except Exception:
        return False


def approve_task(task_id: str) -> bool:
    """タスクを承認して実行可能状態にする。"""
    return update_task(task_id, {
        "status": "executing",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    })


def reject_task(task_id: str) -> bool:
    """タスクを却下する。"""
    return update_task(task_id, {"status": "rejected"})


def save_plan(
    task_id: str,
    plan_markdown: str,
    planned_operations: list[dict],
    saas_context: str = "",
    confidence: float = 0.0,
    warnings: list[str] | None = None,
) -> bool:
    """計画結果をタスクに保存し、status を awaiting_approval に更新。"""
    updates: dict = {
        "plan_markdown": plan_markdown,
        "planned_operations": json.dumps(planned_operations, ensure_ascii=False),
        "operation_count": len(planned_operations),
        "status": "awaiting_approval",
        "plan_confidence": confidence,
        "plan_warnings": json.dumps(warnings or [], ensure_ascii=False),
    }
    if saas_context:
        updates["saas_context"] = saas_context[:50000]
    return update_task(task_id, updates)


def save_result(
    task_id: str,
    result_summary: dict,
    report_markdown: str = "",
    duration_ms: int = 0,
    status: str = "completed",
) -> bool:
    """実行結果サマリーをタスクに保存。"""
    return update_task(task_id, {
        "result_summary": json.dumps(result_summary, ensure_ascii=False),
        "report_markdown": report_markdown,
        "duration_ms": duration_ms,
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })


def record_failure(
    task_id: str,
    failure_reason: str,
    failure_category: str,
    failure_detail: str = "",
) -> bool:
    """タスクの失敗情報を記録。学習システムが参照する。"""
    return update_task(task_id, {
        "status": "failed",
        "failure_reason": failure_reason,
        "failure_category": failure_category,
        "failure_detail": failure_detail or None,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })


def get_similar_failures(
    saas_name: str,
    genre: Optional[str] = None,
    limit: int = 10,
) -> list:
    """同じ SaaS の過去失敗を取得（学習システム用）。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("saas_tasks")
            .select("task_description, failure_reason, failure_category, failure_detail, created_at")
            .eq("saas_name", saas_name)
            .not_.is_("failure_reason", "null")
            .order("created_at", desc=True)
        )
        if genre:
            q = q.eq("genre", genre)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_failure_patterns(
    saas_name: Optional[str] = None,
    min_count: int = 3,
) -> list:
    """失敗パターンを集約して取得（ルール自動生成用）。

    Supabase には GROUP BY の直接 API がないため、
    失敗レコードを取得してアプリ側で集計する。
    """
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("saas_tasks")
            .select("saas_name, genre, failure_reason, failure_category")
            .not_.is_("failure_reason", "null")
            .order("created_at", desc=True)
            .limit(500)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        r = q.execute()
        rows = r.data or []
    except Exception:
        return []

    # アプリ側で集計（正規化して同パターンを集約）
    from collections import Counter
    counter: Counter = Counter()
    details: dict = {}
    for row in rows:
        raw_reason = row.get("failure_reason", "")
        normalized = normalize_failure_reason(raw_reason)
        key = (row.get("saas_name", ""), row.get("failure_category", ""), normalized)
        counter[key] += 1
        if key not in details:
            details[key] = row.get("genre", "")

    patterns = []
    for (sn, cat, reason), count in counter.items():
        if count >= min_count:
            patterns.append({
                "saas_name": sn,
                "failure_category": cat,
                "failure_reason": reason,
                "count": count,
                "genre": details.get((sn, cat, reason), ""),
            })
    return sorted(patterns, key=lambda x: x["count"], reverse=True)


# ---------------------------------------------------------------------------
# 修正履歴（Correction-Driven Learning 用）
# ---------------------------------------------------------------------------


def save_correction(
    company_id: str,
    task_id: str,
    saas_name: str,
    genre: str,
    original_description: str,
    modified_description: str,
    original_plan_markdown: str | None = None,
    original_planned_operations: str = "[]",
    original_confidence: float | None = None,
    original_warnings: str = "[]",
    original_status: str = "",
    original_failure_reason: str | None = None,
    original_failure_category: str | None = None,
    correction_type: str = "description_change",
    user_notes: str | None = None,
) -> str | None:
    """タスク修正履歴を task_corrections テーブルに INSERT する。"""
    client = _get_client()
    if not client:
        return None
    try:
        row = {
            "company_id": company_id,
            "task_id": task_id,
            "saas_name": saas_name,
            "genre": genre or None,
            "original_description": original_description,
            "modified_description": modified_description,
            "original_plan_markdown": original_plan_markdown,
            "original_planned_operations": original_planned_operations,
            "original_confidence": original_confidence,
            "original_warnings": original_warnings,
            "original_status": original_status,
            "original_failure_reason": original_failure_reason,
            "original_failure_category": original_failure_category,
            "correction_type": correction_type,
            "user_notes": user_notes,
            "outcome": "pending",
        }
        r = client.table("task_corrections").insert(row).execute()
        return r.data[0].get("id", "") if r.data else None
    except Exception:
        return None


def update_correction_outcome(task_id: str, outcome: str) -> bool:
    """task_id に紐づく最新の修正記録の outcome を更新する。"""
    client = _get_client()
    if not client:
        return False
    try:
        r = (
            client.table("task_corrections")
            .select("id")
            .eq("task_id", task_id)
            .eq("outcome", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not r.data:
            return False
        correction_id = r.data[0]["id"]
        client.table("task_corrections").update({
            "outcome": outcome,
            "outcome_updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", correction_id).execute()
        return True
    except Exception:
        return False


def _normalize_correction_pattern(original: str, modified: str) -> str:
    """修正の方向性を正規化してパターン集約可能にする。"""
    orig_len = len(original)
    mod_len = len(modified)

    if mod_len > orig_len * 2:
        return "significantly_more_detail"
    import re as _re
    orig_ids = set(_re.findall(r'\d{2,}', original))
    mod_ids = set(_re.findall(r'\d{2,}', modified))
    if mod_ids - orig_ids:
        return "target_id_specified"
    if mod_len > orig_len * 1.3:
        return "detail_added"
    return "rephrased"


def get_correction_patterns(
    saas_name: Optional[str] = None,
    min_count: int = 1,
) -> list:
    """修正パターンを集約して取得（ルール自動生成用）。

    成功した修正のみ学習対象とする。
    """
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("task_corrections")
            .select("saas_name, genre, original_description, modified_description, "
                    "correction_type, outcome")
            .eq("outcome", "completed")
            .order("created_at", desc=True)
            .limit(500)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        r = q.execute()
        rows = r.data or []
    except Exception:
        return []

    from collections import Counter, defaultdict
    counter: Counter = Counter()
    examples: dict = defaultdict(list)

    for row in rows:
        pattern_key = _normalize_correction_pattern(
            row.get("original_description", ""),
            row.get("modified_description", ""),
        )
        key = (row.get("saas_name", ""), row.get("correction_type", ""), pattern_key)
        counter[key] += 1
        if len(examples[key]) < 3:
            examples[key].append({
                "original": row.get("original_description", ""),
                "modified": row.get("modified_description", ""),
                "genre": row.get("genre", ""),
            })

    patterns = []
    for (sn, ctype, pattern), count in counter.items():
        if count >= min_count:
            exs = examples.get((sn, ctype, pattern), [])
            patterns.append({
                "saas_name": sn,
                "correction_type": ctype,
                "pattern_summary": pattern,
                "count": count,
                "genre": exs[0].get("genre", "") if exs else "",
                "examples": exs,
            })
    return sorted(patterns, key=lambda x: x["count"], reverse=True)


def get_dashboard_summary(company_id: str) -> dict:
    """ダッシュボード用サマリーを取得。"""
    client = _get_client()
    if not client:
        return {"total": 0, "pending": 0, "completed": 0, "failed": 0}
    try:
        tasks = get_tasks(company_id, limit=200)
        total = len(tasks)
        pending = sum(1 for t in tasks if t.get("status") == "awaiting_approval")
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        failed = sum(1 for t in tasks if t.get("status") == "failed")
        executing = sum(1 for t in tasks if t.get("status") == "executing")
        return {
            "total": total,
            "awaiting_approval": pending,
            "executing": executing,
            "completed": completed,
            "failed": failed,
        }
    except Exception:
        return {"total": 0, "awaiting_approval": 0, "executing": 0, "completed": 0, "failed": 0}

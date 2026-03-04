"""BPO 専門化成熟度スコアの計算・更新.

ジャンル×SaaS ごとに以下4指標から成熟度を算出する:
- 学習済みルール数（rules/saas/learned/ 内のファイル）
- 完了タスク数（audit_logs の tool 実行回数）
- 成功率（audit_logs の success 比率）
- 平均計画確信度（bpo_task_runs の confidence 平均）

成熟度 ≥ 0.7 で「専門エージェント」として分岐可能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SPECIALIST_THRESHOLD = 0.7


def calculate_maturity_score(saas_name: str, genre: str, company_id: str | None = None) -> dict[str, Any]:
    """ジャンル×SaaS の専門化成熟度を計算する."""
    learned_rules = _count_learned_rules(saas_name, genre)
    total_tasks, success_rate = _get_task_stats(saas_name, genre, company_id)
    avg_confidence = _get_avg_confidence(saas_name, genre, company_id)

    score = (
        min(learned_rules / 10, 1.0) * 0.3
        + min(total_tasks / 50, 1.0) * 0.2
        + max(success_rate - 0.5, 0) / 0.5 * 0.3
        + max(avg_confidence - 0.5, 0) / 0.5 * 0.2
    )
    is_specialist = score >= SPECIALIST_THRESHOLD

    return {
        "saas_name": saas_name,
        "genre": genre,
        "score": round(score, 3),
        "is_specialist": is_specialist,
        "learned_rules_count": learned_rules,
        "total_tasks": total_tasks,
        "success_rate": round(success_rate, 3),
        "avg_confidence": round(avg_confidence, 3),
    }


def recalculate_all(company_id: str | None = None) -> list[dict]:
    """全ジャンル×SaaS の成熟度を再計算して DB に保存する."""
    from server.persist import upsert_maturity_score

    saas_genre_pairs = _get_active_pairs(company_id)
    results = []

    for saas_name, genre in saas_genre_pairs:
        data = calculate_maturity_score(saas_name, genre, company_id)
        upsert_maturity_score(
            saas_name=data["saas_name"],
            genre=data["genre"],
            score=data["score"],
            is_specialist=data["is_specialist"],
            learned_rules_count=data["learned_rules_count"],
            total_tasks=data["total_tasks"],
            success_rate=data["success_rate"],
            avg_confidence=data["avg_confidence"],
            company_id=company_id,
        )
        results.append(data)
        if data["is_specialist"]:
            logger.info(
                "専門エージェント条件達成: %s/%s (score=%.3f)",
                saas_name, genre, data["score"],
            )

    return results


def _count_learned_rules(saas_name: str, genre: str) -> int:
    """rules/saas/learned/ 内の該当ルールファイルの行数ベースでカウント."""
    rules_dir = Path("rules/saas/learned")
    if not rules_dir.exists():
        return 0
    pattern = f"{saas_name}_{genre}_learned.md"
    rule_file = rules_dir / pattern
    if not rule_file.exists():
        return 0
    content = rule_file.read_text(encoding="utf-8").strip()
    if not content:
        return 0
    # "## " で始まるセクション数をルール数とする
    return content.count("\n## ") + (1 if content.startswith("## ") else 0)


def _get_task_stats(saas_name: str, genre: str, company_id: str | None) -> tuple[int, float]:
    """audit_logs から完了タスク数と成功率を取得."""
    try:
        from server._supabase import get_client
        client = get_client()
        if not client:
            return 0, 0.0

        q = (
            client.table("audit_logs")
            .select("result_summary")
            .eq("source", "saas")
            .eq("saas_name", saas_name)
            .eq("genre", genre)
        )
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(1000).execute()
        rows = r.data or []
        if not rows:
            return 0, 0.0

        total = len(rows)
        successes = sum(
            1 for row in rows
            if (row.get("result_summary") or {}).get("success")
        )
        return total, successes / total if total > 0 else 0.0
    except Exception:
        logger.warning("タスク統計取得失敗: %s/%s", saas_name, genre, exc_info=True)
        return 0, 0.0


def _get_avg_confidence(saas_name: str, genre: str, company_id: str | None) -> float:
    """bpo_task_runs から平均確信度を取得."""
    try:
        from server._supabase import get_client
        client = get_client()
        if not client:
            return 0.0

        q = (
            client.table("bpo_task_runs")
            .select("confidence")
            .eq("saas_name", saas_name)
            .eq("genre", genre)
            .not_.is_("confidence", "null")
        )
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(500).execute()
        rows = r.data or []
        if not rows:
            return 0.0

        confidences = [float(row["confidence"]) for row in rows if row.get("confidence") is not None]
        return sum(confidences) / len(confidences) if confidences else 0.0
    except Exception:
        logger.warning("確信度取得失敗: %s/%s", saas_name, genre, exc_info=True)
        return 0.0


def _get_active_pairs(company_id: str | None) -> list[tuple[str, str]]:
    """audit_logs から実際に使われている saas_name × genre のペアを取得."""
    try:
        from server._supabase import get_client
        client = get_client()
        if not client:
            return _fallback_pairs()

        q = (
            client.table("audit_logs")
            .select("saas_name, genre")
            .eq("source", "saas")
        )
        if company_id:
            q = q.eq("company_id", company_id)
        r = q.limit(1000).execute()
        rows = r.data or []
        if not rows:
            return _fallback_pairs()

        pairs = set()
        for row in rows:
            sn = row.get("saas_name")
            g = row.get("genre")
            if sn and g:
                pairs.add((sn, g))
        return list(pairs) if pairs else _fallback_pairs()
    except Exception:
        return _fallback_pairs()


def _fallback_pairs() -> list[tuple[str, str]]:
    """DB に接続できない場合のフォールバック."""
    return [
        ("kintone", "admin"),
        ("salesforce", "sfa"),
        ("freee", "accounting"),
        ("slack", "communication"),
        ("google_workspace", "productivity"),
        ("smarthr", "admin"),
    ]

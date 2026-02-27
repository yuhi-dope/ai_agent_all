"""Pattern Detector — SaaS 操作パターンの自動検出 (Phase 2 "理解").

audit_logs に蓄積された SaaS 操作ログを分析し、以下の4種類のパターンを検出する:

1. 操作頻度分析: 同一ツール×同一アクションの出現頻度を集計
2. シーケンス検出: 時間的に近接する操作の組み合わせをワークフロー候補として検出
3. フィールドマッピング推定: 異なるSaaS間で同一タイミングに操作されたフィールド値の対応関係を推定
4. スキーマ差分検出: saas_schema_snapshots の定期取得による SaaS 側スキーマ変更の検出

検出結果は operation_patterns テーブルに保存し、Phase 3 の自社システム自動生成に利用する。
企業固有データは匿名化し、パターン構造のみを保持する。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 同一シーケンスとみなす操作間の最大間隔（秒）
SEQUENCE_WINDOW_SECONDS = 300  # 5分
# パターンとして認定する最小出現回数
MIN_FREQUENCY_COUNT = 3
# シーケンスパターンの最小出現回数
MIN_SEQUENCE_COUNT = 2


def _get_client():
    """Supabase クライアントを返す。"""
    from server._supabase import get_client

    return get_client()


# ────────────────────────────────────────────────────────
# 1. 操作頻度分析
# ────────────────────────────────────────────────────────


def detect_frequency_patterns(
    company_id: str,
    saas_name: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """audit_logs から同一ツール×アクションの出現頻度を集計し、パターンを検出する。"""
    client = _get_client()
    if not client:
        return []

    try:
        q = (
            client.table("audit_logs")
            .select("tool_name, arguments, saas_name, genre")
            .eq("company_id", company_id)
            .order("logged_at", desc=True)
            .limit(limit)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        r = q.execute()
        rows = r.data or []
    except Exception:
        logger.exception("Failed to fetch audit_logs for frequency analysis")
        return []

    # ツール名 × アクション種別 で集計
    counter: Counter = Counter()
    meta: dict = {}
    for row in rows:
        tool = row.get("tool_name", "")
        args = row.get("arguments") or {}
        action = args.get("action", args.get("method", ""))
        key = (row.get("saas_name", ""), tool, action)
        counter[key] += 1
        if key not in meta:
            meta[key] = {
                "genre": row.get("genre", ""),
                "sample_args": _anonymize_args(args),
            }

    patterns = []
    for (sn, tool, action), count in counter.items():
        if count < MIN_FREQUENCY_COUNT:
            continue
        pattern_key = _hash_key(f"freq:{company_id}:{sn}:{tool}:{action}")
        patterns.append({
            "company_id": company_id,
            "pattern_type": "frequency",
            "saas_name": sn,
            "genre": meta[(sn, tool, action)]["genre"],
            "pattern_key": pattern_key,
            "title": f"{sn or 'sandbox'} - {tool}:{action or '*'}",
            "description": f"{tool} の {action or '(default)'} 操作が {count} 回実行されています",
            "pattern_data": {
                "tool_name": tool,
                "action": action,
                "sample_args": meta[(sn, tool, action)]["sample_args"],
            },
            "occurrence_count": count,
            "confidence": min(1.0, count / 20),
        })

    return patterns


# ────────────────────────────────────────────────────────
# 2. シーケンス検出
# ────────────────────────────────────────────────────────


def detect_sequence_patterns(
    company_id: str,
    saas_name: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """時間的に近接する操作の組み合わせを検出し、ワークフロー候補とする。"""
    client = _get_client()
    if not client:
        return []

    try:
        q = (
            client.table("audit_logs")
            .select("tool_name, arguments, saas_name, genre, logged_at")
            .eq("company_id", company_id)
            .order("logged_at", desc=False)
            .limit(limit)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        r = q.execute()
        rows = r.data or []
    except Exception:
        logger.exception("Failed to fetch audit_logs for sequence analysis")
        return []

    if len(rows) < 2:
        return []

    # 近接する操作ペアを検出
    sequence_counter: Counter = Counter()
    for i in range(len(rows) - 1):
        curr = rows[i]
        nxt = rows[i + 1]
        try:
            t1 = _parse_ts(curr.get("logged_at", ""))
            t2 = _parse_ts(nxt.get("logged_at", ""))
            if t1 is None or t2 is None:
                continue
            delta = (t2 - t1).total_seconds()
        except Exception:
            continue

        if 0 <= delta <= SEQUENCE_WINDOW_SECONDS:
            step_a = f"{curr.get('saas_name', '')}:{curr.get('tool_name', '')}"
            step_b = f"{nxt.get('saas_name', '')}:{nxt.get('tool_name', '')}"
            genre = curr.get("genre") or nxt.get("genre") or ""
            sequence_counter[(step_a, step_b, genre)] += 1

    patterns = []
    for (step_a, step_b, genre), count in sequence_counter.items():
        if count < MIN_SEQUENCE_COUNT:
            continue
        pattern_key = _hash_key(f"seq:{company_id}:{step_a}->{step_b}")
        patterns.append({
            "company_id": company_id,
            "pattern_type": "sequence",
            "saas_name": step_a.split(":")[0] or step_b.split(":")[0],
            "genre": genre,
            "pattern_key": pattern_key,
            "title": f"{step_a} → {step_b}",
            "description": f"操作シーケンス「{step_a} → {step_b}」が {count} 回検出されました。ワークフロー候補です。",
            "pattern_data": {
                "steps": [step_a, step_b],
                "avg_interval_seconds": SEQUENCE_WINDOW_SECONDS,
            },
            "occurrence_count": count,
            "confidence": min(1.0, count / 10),
        })

    return patterns


# ────────────────────────────────────────────────────────
# 3. フィールドマッピング推定
# ────────────────────────────────────────────────────────


def detect_field_mapping_patterns(
    company_id: str,
    limit: int = 500,
) -> list[dict]:
    """異なる SaaS 間で同一タイミングに操作されたフィールド値の対応関係を推定する。"""
    client = _get_client()
    if not client:
        return []

    try:
        r = (
            client.table("audit_logs")
            .select("tool_name, arguments, result_summary, saas_name, genre, logged_at")
            .eq("company_id", company_id)
            .not_.is_("saas_name", "null")
            .order("logged_at", desc=False)
            .limit(limit)
        ).execute()
        rows = r.data or []
    except Exception:
        logger.exception("Failed to fetch audit_logs for field mapping")
        return []

    if len(rows) < 2:
        return []

    # 時間的に近接する異なるSaaS操作間のフィールド値を比較
    mapping_counter: Counter = Counter()
    mapping_meta: dict = {}

    for i in range(len(rows) - 1):
        curr = rows[i]
        nxt = rows[i + 1]
        if curr.get("saas_name") == nxt.get("saas_name"):
            continue
        try:
            t1 = _parse_ts(curr.get("logged_at", ""))
            t2 = _parse_ts(nxt.get("logged_at", ""))
            if t1 is None or t2 is None:
                continue
            delta = (t2 - t1).total_seconds()
        except Exception:
            continue

        if 0 <= delta <= SEQUENCE_WINDOW_SECONDS:
            curr_fields = _extract_field_names(curr.get("arguments") or {})
            nxt_fields = _extract_field_names(nxt.get("arguments") or {})
            # フィールド名の共通部分 = マッピング候補
            common = curr_fields & nxt_fields
            for field in common:
                key = (curr.get("saas_name", ""), nxt.get("saas_name", ""), field)
                mapping_counter[key] += 1
                if key not in mapping_meta:
                    mapping_meta[key] = curr.get("genre") or nxt.get("genre") or ""

    patterns = []
    for (saas_a, saas_b, field), count in mapping_counter.items():
        if count < MIN_SEQUENCE_COUNT:
            continue
        pattern_key = _hash_key(f"fmap:{company_id}:{saas_a}:{saas_b}:{field}")
        patterns.append({
            "company_id": company_id,
            "pattern_type": "field_mapping",
            "saas_name": saas_a,
            "genre": mapping_meta.get((saas_a, saas_b, field), ""),
            "pattern_key": pattern_key,
            "title": f"{saas_a}.{field} ↔ {saas_b}.{field}",
            "description": f"{saas_a} と {saas_b} の間でフィールド「{field}」が {count} 回同時に操作されました。データ連携候補です。",
            "pattern_data": {
                "source_saas": saas_a,
                "target_saas": saas_b,
                "field_name": field,
            },
            "occurrence_count": count,
            "confidence": min(1.0, count / 5),
        })

    return patterns


# ────────────────────────────────────────────────────────
# 4. スキーマ差分検出
# ────────────────────────────────────────────────────────


def detect_schema_diff_patterns(
    company_id: str,
    saas_name: Optional[str] = None,
) -> list[dict]:
    """saas_schema_snapshots の最新2件を比較し、スキーマ変更を検出する。"""
    client = _get_client()
    if not client:
        return []

    try:
        q = (
            client.table("saas_schema_snapshots")
            .select("saas_name, schema_data, snapshot_hash, created_at")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(100)
        )
        if saas_name:
            q = q.eq("saas_name", saas_name)
        r = q.execute()
        rows = r.data or []
    except Exception:
        logger.exception("Failed to fetch schema snapshots")
        return []

    # SaaS ごとに最新2件を比較
    by_saas: dict[str, list] = defaultdict(list)
    for row in rows:
        sn = row.get("saas_name", "")
        by_saas[sn].append(row)

    patterns = []
    for sn, snapshots in by_saas.items():
        if len(snapshots) < 2:
            continue
        latest = snapshots[0]
        previous = snapshots[1]
        if latest.get("snapshot_hash") == previous.get("snapshot_hash"):
            continue

        # 差分を計算
        diff = _compute_schema_diff(
            previous.get("schema_data") or {},
            latest.get("schema_data") or {},
        )
        if not diff:
            continue

        pattern_key = _hash_key(
            f"sdiff:{company_id}:{sn}:{latest.get('snapshot_hash', '')}"
        )
        patterns.append({
            "company_id": company_id,
            "pattern_type": "schema_diff",
            "saas_name": sn,
            "genre": "",
            "pattern_key": pattern_key,
            "title": f"{sn} スキーマ変更検出",
            "description": f"{sn} のスキーマに変更が検出されました: {', '.join(diff['summary'][:3])}",
            "pattern_data": diff,
            "occurrence_count": 1,
            "confidence": 0.9,
        })

    return patterns


# ────────────────────────────────────────────────────────
# 統合実行
# ────────────────────────────────────────────────────────


def run_detection(
    company_id: str,
    saas_name: Optional[str] = None,
) -> dict:
    """全パターン検出を実行し、結果を operation_patterns テーブルに保存する。

    Returns:
        検出結果のサマリー。
    """
    all_patterns: list[dict] = []

    all_patterns.extend(detect_frequency_patterns(company_id, saas_name))
    all_patterns.extend(detect_sequence_patterns(company_id, saas_name))
    all_patterns.extend(detect_field_mapping_patterns(company_id))
    all_patterns.extend(detect_schema_diff_patterns(company_id, saas_name))

    saved = 0
    updated = 0
    for p in all_patterns:
        result = _upsert_pattern(p)
        if result == "created":
            saved += 1
        elif result == "updated":
            updated += 1

    summary = {
        "company_id": company_id,
        "total_detected": len(all_patterns),
        "saved": saved,
        "updated": updated,
        "by_type": {
            pt: sum(1 for p in all_patterns if p["pattern_type"] == pt)
            for pt in ("frequency", "sequence", "field_mapping", "schema_diff")
        },
    }
    logger.info("Pattern detection completed: %s", summary)
    return summary


def get_patterns(
    company_id: str,
    pattern_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """保存済みのパターン一覧を取得する。"""
    client = _get_client()
    if not client:
        return []
    try:
        q = (
            client.table("operation_patterns")
            .select("*")
            .eq("company_id", company_id)
            .order("occurrence_count", desc=True)
        )
        if pattern_type:
            q = q.eq("pattern_type", pattern_type)
        if status:
            q = q.eq("status", status)
        r = q.limit(limit).execute()
        return list(r.data) if r.data else []
    except Exception:
        return []


def save_schema_snapshot(
    company_id: str,
    saas_name: str,
    schema_data: dict,
    connection_id: Optional[str] = None,
) -> bool:
    """SaaS スキーマスナップショットを保存する。"""
    client = _get_client()
    if not client:
        return False
    try:
        snapshot_hash = hashlib.sha256(
            json.dumps(schema_data, sort_keys=True).encode()
        ).hexdigest()[:16]
        row = {
            "company_id": company_id,
            "saas_name": saas_name,
            "schema_data": schema_data,
            "snapshot_hash": snapshot_hash,
        }
        if connection_id:
            row["connection_id"] = connection_id
        client.table("saas_schema_snapshots").insert(row).execute()
        return True
    except Exception:
        logger.exception("Failed to save schema snapshot")
        return False


# ────────────────────────────────────────────────────────
# 内部ヘルパー
# ────────────────────────────────────────────────────────


def _upsert_pattern(pattern: dict) -> str:
    """パターンを operation_patterns に upsert する。"""
    client = _get_client()
    if not client:
        return "error"
    try:
        existing = (
            client.table("operation_patterns")
            .select("id, occurrence_count")
            .eq("pattern_key", pattern["pattern_key"])
            .limit(1)
            .execute()
        )
        if existing.data:
            # 更新
            client.table("operation_patterns").update({
                "occurrence_count": pattern["occurrence_count"],
                "confidence": pattern["confidence"],
                "pattern_data": pattern["pattern_data"],
                "description": pattern["description"],
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", existing.data[0]["id"]).execute()
            return "updated"
        else:
            # 新規
            row = {
                "company_id": pattern["company_id"],
                "pattern_type": pattern["pattern_type"],
                "saas_name": pattern.get("saas_name"),
                "genre": pattern.get("genre"),
                "pattern_key": pattern["pattern_key"],
                "title": pattern["title"],
                "description": pattern["description"],
                "pattern_data": pattern["pattern_data"],
                "occurrence_count": pattern["occurrence_count"],
                "confidence": pattern["confidence"],
            }
            client.table("operation_patterns").insert(row).execute()
            return "created"
    except Exception:
        logger.exception("Failed to upsert pattern")
        return "error"


def _hash_key(raw: str) -> str:
    """パターンキーのハッシュを生成。"""
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _anonymize_args(args: dict) -> dict:
    """引数から企業固有データを除去し、構造のみ保持する。"""
    if not isinstance(args, dict):
        return {}
    return {k: type(v).__name__ for k, v in args.items()}


def _extract_field_names(args: dict) -> set[str]:
    """引数からフィールド名を抽出する。"""
    fields: set[str] = set()
    if not isinstance(args, dict):
        return fields
    for key, val in args.items():
        if key in ("action", "method", "timeout_seconds"):
            continue
        fields.add(key)
        if isinstance(val, dict):
            fields.update(val.keys())
    return fields


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """タイムスタンプ文字列をパースする。"""
    if not ts_str:
        return None
    try:
        # ISO 8601
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def _compute_schema_diff(old: dict, new: dict) -> dict:
    """2つのスキーマ間の差分を計算する。"""
    added = set(new.keys()) - set(old.keys())
    removed = set(old.keys()) - set(new.keys())
    modified = []
    for key in set(old.keys()) & set(new.keys()):
        if json.dumps(old[key], sort_keys=True) != json.dumps(new[key], sort_keys=True):
            modified.append(key)

    if not added and not removed and not modified:
        return {}

    summary = []
    if added:
        summary.append(f"追加: {', '.join(sorted(added)[:5])}")
    if removed:
        summary.append(f"削除: {', '.join(sorted(removed)[:5])}")
    if modified:
        summary.append(f"変更: {', '.join(sorted(modified)[:5])}")

    return {
        "added_objects": sorted(added),
        "removed_objects": sorted(removed),
        "modified_objects": sorted(modified),
        "summary": summary,
    }

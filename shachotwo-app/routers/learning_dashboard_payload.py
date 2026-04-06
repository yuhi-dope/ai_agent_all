"""学習ダッシュボード API レスポンスの純粋整形（FastAPI に依存しない）。"""

from collections import Counter, defaultdict
from typing import Any, Optional


def _month_key_from_period(period_val: Any) -> Optional[str]:
    """DATE / 文字列から YYYY-MM を返す。"""
    if period_val is None:
        return None
    s = str(period_val)
    if len(s) >= 7:
        return s[:7]
    return None


def _month_key_from_created(created_val: Any) -> Optional[str]:
    """TIMESTAMPTZ 文字列から YYYY-MM を返す。"""
    if created_val is None:
        return None
    s = str(created_val)
    if len(s) >= 7:
        return s[:7]
    return None


def _extract_improvement_pp(metrics: Any) -> Optional[float]:
    if not metrics or not isinstance(metrics, dict):
        return None
    for key in ("improvement_since_last_update", "improvement_pp", "delta_accuracy"):
        v = metrics.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _aggregate_outreach_monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """outreach_performance 行を月次でフロントの outreach_pdca 形にまとめる。"""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        mk = _month_key_from_period(row.get("period"))
        if not mk:
            continue
        b = buckets.setdefault(
            mk,
            {
                "sent": 0,
                "open_num": 0.0,
                "reply_num": 0.0,
                "meetings": 0,
                "by_industry": defaultdict(lambda: {"sent": 0, "meetings": 0}),
            },
        )
        oc = int(row.get("outreached_count") or 0)
        b["sent"] += oc
        orate = float(row.get("open_rate") or 0)
        crate = float(row.get("click_rate") or 0)
        b["open_num"] += orate * oc
        b["reply_num"] += crate * oc
        mb = int(row.get("meeting_booked_count") or 0)
        b["meetings"] += mb
        ind = row.get("industry") or "unknown"
        b["by_industry"][ind]["sent"] += oc
        b["by_industry"][ind]["meetings"] += mb

    result: list[dict[str, Any]] = []
    for mk in sorted(buckets.keys()):
        b = buckets[mk]
        sent = int(b["sent"])
        open_rate = round(b["open_num"] / sent, 4) if sent else 0.0
        reply_rate = round(b["reply_num"] / sent, 4) if sent else 0.0
        meeting_rate = round(b["meetings"] / sent, 4) if sent else 0.0

        best_ind: Optional[str] = None
        best_mr = -1.0
        for ind, rec in b["by_industry"].items():
            s_ind = int(rec["sent"])
            mr = rec["meetings"] / s_ind if s_ind else 0.0
            if mr > best_mr:
                best_mr = mr
                best_ind = str(ind)

        top_subject: Optional[str] = None
        if rows:
            month_rows = [
                r
                for r in rows
                if _month_key_from_period(r.get("period")) == mk
                and int(r.get("meeting_booked_count") or 0) > 0
                and r.get("email_variant")
            ]
            if month_rows:
                top_subject = str(month_rows[0].get("email_variant"))

        result.append(
            {
                "period": mk,
                "sent_count": sent,
                "open_rate": open_rate,
                "reply_rate": reply_rate,
                "meeting_rate": meeting_rate,
                "top_performing_industry": best_ind if best_mr > 0 else None,
                "top_performing_subject": top_subject,
            }
        )
    return result


def _aggregate_cs_monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """cs_feedback 行を月次でフロントの cs_quality 形にまとめる。"""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        mk = _month_key_from_created(row.get("created_at"))
        if not mk:
            continue
        b = buckets.setdefault(
            mk,
            {"n": 0, "esc": 0, "csat_sum": 0.0, "csat_n": 0},
        )
        b["n"] += 1
        if row.get("was_escalated"):
            b["esc"] += 1
        csat = row.get("csat_score")
        if csat is not None:
            try:
                b["csat_sum"] += float(csat)
                b["csat_n"] += 1
            except (TypeError, ValueError):
                pass

    result: list[dict[str, Any]] = []
    for mk in sorted(buckets.keys()):
        b = buckets[mk]
        n = int(b["n"])
        esc = int(b["esc"])
        ai_resolution = round((n - esc) / n, 4) if n else 0.0
        escalation_rate = round(esc / n, 4) if n else 0.0
        cn = int(b["csat_n"])
        avg_csat = round(b["csat_sum"] / cn, 2) if cn else None
        result.append(
            {
                "period": mk,
                "ai_resolution_rate": ai_resolution,
                "avg_csat": avg_csat,
                "avg_frt_min": None,
                "escalation_rate": escalation_rate,
            }
        )
    return result


def _patterns_from_win_loss(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """win_loss_patterns から won_patterns / lost_patterns を生成。"""
    ind_won: dict[str, int] = defaultdict(int)
    ind_total: dict[str, int] = defaultdict(int)
    lost_reasons: Counter[str] = Counter()

    for row in rows:
        ind = row.get("industry") or "未分類"
        ind_key = str(ind)
        ind_total[ind_key] += 1
        outcome = row.get("outcome")
        if outcome == "won":
            ind_won[ind_key] += 1
        elif outcome == "lost":
            lr = row.get("lost_reason")
            lost_reasons[str(lr).strip() if lr else "理由未入力"] += 1

    won_patterns: list[dict[str, Any]] = []
    for ind, w in sorted(ind_won.items(), key=lambda x: -x[1])[:15]:
        t = ind_total[ind]
        won_patterns.append(
            {
                "pattern": ind,
                "count": w,
                "win_rate": round(w / t, 4) if t else 0.0,
                "description": f"{ind} セグメントにおける受注傾向（全{t}件中{w}件が受注）",
            }
        )

    total_lost = sum(lost_reasons.values())
    lost_patterns: list[dict[str, Any]] = []
    if total_lost > 0:
        for reason, cnt in lost_reasons.most_common(15):
            lost_patterns.append(
                {
                    "reason": reason,
                    "count": cnt,
                    "percentage": round(cnt / total_lost, 4),
                    "suggested_improvement": "失注理由をヒアリングし、提案・価格・機能のギャップを整理してください。",
                }
            )

    return won_patterns, lost_patterns


def build_learning_dashboard_payload(
    scoring: Optional[dict[str, Any]],
    outreach_rows: list[dict[str, Any]],
    win_loss_rows: list[dict[str, Any]],
    cs_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """GET /learning/dashboard のレスポンス形（フロント LearningDashboard と一致）。"""
    outreach_pdca = _aggregate_outreach_monthly(outreach_rows)
    cs_quality = _aggregate_cs_monthly(cs_rows)
    won_patterns, lost_patterns = _patterns_from_win_loss(win_loss_rows)
    training_n = len(win_loss_rows)

    version_label = "未登録"
    last_updated: Optional[str] = None
    improvement: Optional[float] = None
    if scoring:
        mt = scoring.get("model_type") or "lead_score"
        ver = scoring.get("version")
        version_label = f"{mt}-v{ver}" if ver is not None else str(mt)
        ca = scoring.get("created_at")
        if ca is not None:
            last_updated = str(ca)
        improvement = _extract_improvement_pp(scoring.get("performance_metrics"))

    return {
        "scoring_accuracy": [],
        "outreach_pdca": outreach_pdca,
        "cs_quality": cs_quality,
        "won_patterns": won_patterns,
        "lost_patterns": lost_patterns,
        "learning_loop_status": {
            "last_model_updated_at": last_updated,
            "scoring_model_version": version_label,
            "total_training_samples": training_n,
            "improvement_since_last_update": improvement,
        },
    }

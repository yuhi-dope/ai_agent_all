"""execution_logs から精度レポートを集計する。"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# 降格時に min_confidence_for_auto を引き上げる幅
_DEGRADATION_CONFIDENCE_BUMP = 0.05
# min_confidence_for_auto の上限（NOTIFY_ONLY相当: これ以上は上げない）
_MAX_MIN_CONFIDENCE = 1.0


@dataclass
class StepAccuracyReport:
    pipeline: str
    step_name: str
    avg_confidence: float
    call_count: int
    low_confidence_count: int   # confidence < 0.8
    feedback_negative_count: int
    needs_improvement: bool     # avg_confidence < 0.75 かつ call_count >= 5
    trend: str = "stable"       # "improving" | "stable" | "declining"


@dataclass
class DegradationResponse:
    """check_and_respond_to_degradation の結果。"""
    demoted_pipelines: list[str]
    notified: bool
    skipped_pipelines: list[str]  # 既に上限（min_confidence=1.0）のため降格不要


async def _compute_trend(
    pipeline: str,
    step_name: str,
    company_id: str | None,
) -> str:
    """直近7日 vs その前7日の avg_confidence を比較してトレンドを返す。

    差が -0.03 以下 → "declining"
    差が +0.03 以上 → "improving"
    それ以外       → "stable"
    """
    db = get_service_client()
    now = datetime.now(timezone.utc)
    recent_since = (now - timedelta(days=7)).isoformat()
    prev_since = (now - timedelta(days=14)).isoformat()
    prev_until = recent_since

    def _query_confidences(since: str, until: str | None) -> list[float]:
        query = db.table("execution_logs").select("operations, created_at")
        if company_id:
            query = query.eq("company_id", company_id)
        query = query.gte("created_at", since)
        if until:
            query = query.lt("created_at", until)
        result = query.execute()
        confs: list[float] = []
        for row in result.data or []:
            ops = row.get("operations") or {}
            if ops.get("pipeline") != pipeline:
                continue
            for step in ops.get("steps", []):
                if step.get("step") != step_name:
                    continue
                conf = step.get("confidence")
                if conf is not None:
                    confs.append(float(conf))
        return confs

    recent_confs = _query_confidences(recent_since, None)
    prev_confs = _query_confidences(prev_since, prev_until)

    if not recent_confs or not prev_confs:
        return "stable"

    recent_avg = sum(recent_confs) / len(recent_confs)
    prev_avg = sum(prev_confs) / len(prev_confs)
    diff = recent_avg - prev_avg

    if diff <= -0.03:
        return "declining"
    if diff >= 0.03:
        return "improving"
    return "stable"


async def get_accuracy_report(
    company_id: str | None = None,
    days: int = 7,
) -> list[StepAccuracyReport]:
    """直近N日の execution_logs からステップ別精度レポートを生成。

    各レポートに `trend` フィールドを付与する（"improving"/"stable"/"declining"）。
    trend は直近7日 vs その前7日の avg_confidence の差分で判定する。
    """
    db = get_service_client()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    query = db.table("execution_logs").select("operations, overall_success, created_at")
    if company_id:
        query = query.eq("company_id", company_id)
    result = query.gte("created_at", since).execute()

    # pipeline+step をキーに集計
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for row in result.data or []:
        ops = row.get("operations") or {}
        pipeline = ops.get("pipeline", "unknown")
        for step in ops.get("steps", []):
            key = (pipeline, step.get("step", "unknown"))
            if key not in agg:
                agg[key] = {"confidences": [], "neg_fb": 0}
            conf = step.get("confidence")
            if conf is not None:
                agg[key]["confidences"].append(float(conf))
        # フィードバック
        fb = ops.get("feedback", {})
        if fb.get("rating") == "bad":
            for step in ops.get("steps", []):
                key = (pipeline, step.get("step", "unknown"))
                if key in agg:
                    agg[key]["neg_fb"] += 1

    reports = []
    for (pipeline, step_name), data in agg.items():
        confs = data["confidences"]
        if not confs:
            continue
        avg = sum(confs) / len(confs)
        low = sum(1 for c in confs if c < 0.8)
        trend = await _compute_trend(pipeline, step_name, company_id)
        reports.append(StepAccuracyReport(
            pipeline=pipeline,
            step_name=step_name,
            avg_confidence=round(avg, 4),
            call_count=len(confs),
            low_confidence_count=low,
            feedback_negative_count=data["neg_fb"],
            needs_improvement=(avg < 0.75 and len(confs) >= 5),
            trend=trend,
        ))
    return sorted(reports, key=lambda r: r.avg_confidence)


async def _demote_pipeline(pipeline_key: str) -> bool:
    """bpo_hitl_requirements テーブルでパイプラインの min_confidence_for_auto を引き上げる。

    - エントリが存在しない場合は新規作成（min_confidence_for_auto=0.95 から開始）
    - 既に min_confidence_for_auto >= 1.0 (NOTIFY_ONLY相当) なら降格しない → False を返す
    - 降格成功時は True を返す
    """
    db = get_service_client()

    result = (
        db.table("bpo_hitl_requirements")
        .select("id, min_confidence_for_auto")
        .eq("pipeline_key", pipeline_key)
        .execute()
    )
    rows = result.data or []

    if not rows:
        # エントリなし → 新規作成（min_confidence_for_auto=0.95、requires_approval=True）
        db.table("bpo_hitl_requirements").insert({
            "pipeline_key": pipeline_key,
            "requires_approval": True,
            "min_confidence_for_auto": 0.95,
            "description": f"精度劣化により自動生成: {pipeline_key}",
        }).execute()
        logger.info(
            "accuracy_monitor: 降格 pipeline=%s 新規エントリ作成 min_confidence_for_auto=0.95",
            pipeline_key,
        )
        return True

    row = rows[0]
    current = row.get("min_confidence_for_auto")

    # None は「常にHitL」= 既に最厳格なので降格不要
    if current is None:
        logger.info(
            "accuracy_monitor: pipeline=%s は min_confidence_for_auto=NULL (常にHitL) のため降格スキップ",
            pipeline_key,
        )
        return False

    new_value = round(min(float(current) + _DEGRADATION_CONFIDENCE_BUMP, _MAX_MIN_CONFIDENCE), 4)
    if new_value >= _MAX_MIN_CONFIDENCE:
        logger.info(
            "accuracy_monitor: pipeline=%s は min_confidence_for_auto が上限 (%.2f) に到達済み。降格スキップ",
            pipeline_key,
            current,
        )
        return False

    db.table("bpo_hitl_requirements").update({
        "min_confidence_for_auto": new_value,
    }).eq("pipeline_key", pipeline_key).execute()
    logger.info(
        "accuracy_monitor: 降格 pipeline=%s %.4f → %.4f",
        pipeline_key,
        current,
        new_value,
    )
    return True


async def check_and_respond_to_degradation(company_id: str) -> DegradationResponse:
    """精度劣化を検知し、自動降格とメール通知を実行する。

    処理フロー:
    1. get_accuracy_report() で直近7日のステップ別精度レポートを取得
    2. needs_improvement=True かつ trend="declining" のステップを抽出
    3. 対象パイプラインについて:
       a. bpo_hitl_requirements テーブルの min_confidence_for_auto を引き上げ（降格）
       b. notify_pipeline_event() で event_type="degradation" の通知を送信
    4. 結果を DegradationResponse で返す

    Args:
        company_id: 処理対象の会社ID

    Returns:
        DegradationResponse: 降格したパイプライン一覧・通知結果・スキップ一覧
    """
    from workers.bpo.manager.notifier import notify_pipeline_event

    reports = await get_accuracy_report(company_id=company_id, days=7)

    # needs_improvement=True かつ trend="declining" のステップを抽出
    declining_steps = [r for r in reports if r.needs_improvement and r.trend == "declining"]

    if not declining_steps:
        logger.info(
            "accuracy_monitor: company_id=%s 精度劣化ステップなし",
            company_id[:8],
        )
        return DegradationResponse(
            demoted_pipelines=[],
            notified=False,
            skipped_pipelines=[],
        )

    # パイプライン単位で集約（同一パイプラインの複数ステップは1回だけ処理）
    pipeline_to_steps: dict[str, list[StepAccuracyReport]] = {}
    for step in declining_steps:
        pipeline_to_steps.setdefault(step.pipeline, []).append(step)

    demoted: list[str] = []
    skipped: list[str] = []
    any_notified = False

    for pipeline, steps in pipeline_to_steps.items():
        # 代表値としてステップ avg_confidence の平均を使用
        avg_conf = round(sum(s.avg_confidence for s in steps) / len(steps), 4)

        demoted_ok = await _demote_pipeline(pipeline)

        if demoted_ok:
            demoted.append(pipeline)
        else:
            skipped.append(pipeline)

        # 降格の成否に関わらず通知（スキップの場合も管理者に知らせる）
        details: dict[str, Any] = {
            "pipeline": pipeline,
            "avg_confidence": avg_conf,
            "trend": "declining",
            "declining_steps": ", ".join(s.step_name for s in steps),
            "action": "min_confidence_for_auto 引き上げ" if demoted_ok else "既に上限のためスキップ",
        }
        notified = await notify_pipeline_event(
            company_id=company_id,
            pipeline=pipeline,
            event_type="degradation",
            details=details,
        )
        if notified:
            any_notified = True

        logger.info(
            "accuracy_monitor: company_id=%s pipeline=%s avg_conf=%.4f demoted=%s notified=%s",
            company_id[:8],
            pipeline,
            avg_conf,
            demoted_ok,
            notified,
        )

    return DegradationResponse(
        demoted_pipelines=demoted,
        notified=any_notified,
        skipped_pipelines=skipped,
    )


async def run_accuracy_check_pipeline(
    company_id: str,
    input_data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """精度監視パイプライン関数（PIPELINE_REGISTRY 経由で呼ばれる）。

    schedule_watcher の毎日 21:00 トリガーから実行される。
    1. get_accuracy_report() で直近7日のレポートを取得
    2. needs_improvement=True のステップを抽出
    3. check_and_respond_to_degradation() で自動降格・通知を実行
    4. 結果を dict で返す

    Returns:
        {
            "success": bool,
            "declining_pipelines": list[str],   # 降格されたパイプライン
            "skipped_pipelines": list[str],      # 既に上限のためスキップ
            "notified": bool,
            "total_steps_checked": int,
            "needs_improvement_count": int,
        }
    """
    logger.info("run_accuracy_check_pipeline: start company_id=%s", company_id[:8])

    try:
        reports = await get_accuracy_report(company_id=company_id, days=7)

        needs_improvement = [r for r in reports if r.needs_improvement]
        logger.info(
            "run_accuracy_check_pipeline: %d steps checked, %d need improvement",
            len(reports),
            len(needs_improvement),
        )

        degradation = await check_and_respond_to_degradation(company_id=company_id)

        return {
            "success": True,
            "declining_pipelines": degradation.demoted_pipelines,
            "skipped_pipelines": degradation.skipped_pipelines,
            "notified": degradation.notified,
            "total_steps_checked": len(reports),
            "needs_improvement_count": len(needs_improvement),
        }

    except Exception as e:
        logger.error("run_accuracy_check_pipeline: error company_id=%s — %s", company_id[:8], e)
        return {
            "success": False,
            "declining_pipelines": [],
            "skipped_pipelines": [],
            "notified": False,
            "total_steps_checked": 0,
            "needs_improvement_count": 0,
            "error": str(e),
        }

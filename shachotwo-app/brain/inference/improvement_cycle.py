"""精度向上サイクル: モニタリング → 改善案生成 → (dry_run=False なら) ファイル更新。"""
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from brain.inference.accuracy_monitor import get_accuracy_report
from brain.inference.prompt_optimizer import optimize_prompt, save_prompt_version
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# llm/prompts/ の絶対パスを解決するためのベースディレクトリ
# このファイルは shachotwo-app/brain/inference/ にあるので、2階層上が shachotwo-app/
_APP_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PROMPTS_DIR = os.path.join(_APP_BASE_DIR, "llm", "prompts")


async def record_negative_feedback(
    execution_id: str,
    company_id: str,
    comment: str = "",
    step_feedbacks: list[dict] | None = None,
) -> None:
    """否定フィードバックを受けた実行ログを improvement_cycle 用に記録する。

    fire-and-forget 想定: 例外は握りつぶしてログ出力のみ。
    将来的には precision_review キューや Slack 通知に拡張。
    """
    try:
        db = get_service_client()
        row = (
            db.table("execution_logs")
            .select("operations")
            .eq("id", execution_id)
            .eq("company_id", company_id)
            .maybe_single()
            .execute()
        )
        if not row or not row.data:
            logger.warning("record_negative_feedback: execution_id=%s not found", execution_id)
            return

        ops: dict = row.data.get("operations") or {}
        # フィードバック情報を operations.negative_feedback に追記
        existing: list = ops.get("negative_feedback", [])
        existing.append({
            "comment": comment,
            "step_feedbacks": step_feedbacks or [],
        })
        ops["negative_feedback"] = existing

        db.table("execution_logs").update({"operations": ops}).eq("id", execution_id).execute()
        logger.info(
            "record_negative_feedback: recorded for execution_id=%s pipeline=%s",
            execution_id,
            ops.get("pipeline", "unknown"),
        )
    except Exception:
        logger.exception("record_negative_feedback failed for execution_id=%s", execution_id)


async def run_improvement_cycle(
    company_id: str | None = None,
    confidence_threshold: float = 0.75,
    min_calls: int = 5,
    dry_run: bool = True,
    days: int = 7,
    apply_confidence_threshold: float | None = None,
) -> dict[str, Any]:
    """
    精度向上サイクルを実行。
    dry_run=True の場合は改善案を返すだけでファイル変更なし（デフォルト）。

    Args:
        company_id: 対象テナントID（None の場合は全テナント）
        confidence_threshold: 改善対象と判断する信頼度しきい値
        min_calls: 改善対象とするための最小呼び出し回数
        dry_run: True の場合はファイル変更なし（提案のみ）
        days: 分析対象日数
        apply_confidence_threshold: dry_run=False の場合に適用する最小改善提案信頼度。
            None の場合はしきい値チェックなし（全件適用）。
    """
    # execution_logs の参照範囲を直近30日に固定（daysが30より大きい場合も30日に制限）
    effective_days = min(days, 30)
    reports = await get_accuracy_report(company_id=company_id, days=effective_days)
    targets = [r for r in reports if r.needs_improvement]

    improved: list[dict] = []
    skipped: list[dict] = []
    total_cost = 0.0

    # 7日以内に改善済みのステップをスキップするための判定基準日時
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    for report in targets:
        # 改善済みスキップ判定: prompt_versions の最新エントリの created_at を確認
        improvement_applied_at = _get_last_improvement_applied_at(
            report.pipeline, report.step_name, company_id=company_id
        )
        if improvement_applied_at is not None and improvement_applied_at > seven_days_ago:
            skip_entry: dict[str, Any] = {
                "pipeline": report.pipeline,
                "step_name": report.step_name,
                "avg_confidence_before": report.avg_confidence,
                "applied": False,
                "skip_reason": "recently_improved",
                "improvement_applied_at": improvement_applied_at.isoformat(),
            }
            skipped.append(skip_entry)
            logger.info(
                "improvement_cycle: skip %s/%s (recently_improved at %s)",
                report.pipeline, report.step_name, improvement_applied_at.isoformat(),
            )
            # improvement_skip_reason を execution_logs に記録
            _record_skip_reason(
                pipeline=report.pipeline,
                step_name=report.step_name,
                company_id=company_id,
                skip_reason="recently_improved",
            )
            continue

        failing = _collect_failing_examples(report.pipeline, report.step_name, company_id=company_id)
        current_prompt = _read_current_prompt(report.pipeline, report.step_name)

        suggestion = await optimize_prompt(
            pipeline=report.pipeline,
            step_name=report.step_name,
            failing_examples=failing,
            current_prompt=current_prompt,
        )

        if suggestion:
            entry: dict[str, Any] = {
                "pipeline": report.pipeline,
                "step_name": report.step_name,
                "avg_confidence_before": report.avg_confidence,
                "suggestion_preview": suggestion[:200],
                "applied": False,
                "skip_reason": None,
            }
            if not dry_run:
                # apply_confidence_threshold が指定されている場合は確信度チェック
                # optimize_prompt は単純な文字列を返すため、ここでは提案の長さと内容で
                # 暫定的な信頼度を推定する（空でなければ適用候補）
                if apply_confidence_threshold is not None:
                    # 提案内容が十分な長さ（200文字以上）かつ特定のキーワードがあれば高信頼度と判断
                    estimated_confidence = _estimate_suggestion_confidence(suggestion)
                    if estimated_confidence < apply_confidence_threshold:
                        entry["skip_reason"] = (
                            f"estimated_confidence={estimated_confidence:.2f} < "
                            f"threshold={apply_confidence_threshold}"
                        )
                        skipped.append(entry)
                        logger.info(
                            "improvement_cycle: skip %s/%s (confidence too low: %.2f)",
                            report.pipeline, report.step_name, estimated_confidence,
                        )
                        continue

                _write_prompt_suggestion(
                    report.pipeline, report.step_name, suggestion, dry_run=False
                )
                # DBにバージョンを保存（accuracy_before を記録し、after は後続モニタリングで更新）
                try:
                    version_id = await save_prompt_version(
                        pipeline=report.pipeline,
                        step_name=report.step_name,
                        prompt_text=suggestion,
                        accuracy_before=report.avg_confidence,
                        accuracy_after=None,
                        change_reason=(
                            f"自動改善サイクル: 低信頼度スコア {report.avg_confidence:.3f}"
                        ),
                        company_id=company_id,
                        created_by="improvement_cycle",
                    )
                    entry["prompt_version_id"] = version_id
                    # improvement_applied_at を現在時刻で更新
                    _update_improvement_applied_at(
                        pipeline=report.pipeline,
                        step_name=report.step_name,
                        company_id=company_id,
                        applied_at=datetime.now(timezone.utc),
                    )
                except Exception as exc:
                    logger.warning(
                        "improvement_cycle: save_prompt_version failed for %s/%s: %s",
                        report.pipeline, report.step_name, exc,
                    )
                entry["applied"] = True
                logger.info("Prompt updated: %s/%s", report.pipeline, report.step_name)
            # dry_run=True の場合はファイル操作を一切行わない（提案を返すだけ）
            improved.append(entry)

    return {
        "checked_steps": len(reports),
        "improvement_targets": len(targets),
        "improved_steps": improved,
        "skipped_steps": skipped,
        "dry_run": dry_run,
        "company_id": company_id,
    }


async def run_auto_improvement_cycle(
    company_id: str,
    input_data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """自動プロンプト改善サイクル（confidence >= 0.8 の提案のみ適用）。

    schedule_watcher の週次トリガーから呼び出される。

    処理フロー:
    1. run_improvement_cycle(dry_run=False, apply_confidence_threshold=0.8) を実行
    2. 適用した改善を execution_logs に記録
    3. 完了通知メールを送信

    Args:
        company_id: 対象テナントID
        input_data: schedule_watcher から渡される追加パラメータ（現状は未使用）
    """
    from workers.bpo.manager.notifier import notify_pipeline_event

    start_time = datetime.now(timezone.utc)
    log_id = str(uuid.uuid4())

    logger.info("run_auto_improvement_cycle: start company_id=%s log_id=%s", company_id[:8], log_id)

    try:
        result = await run_improvement_cycle(
            company_id=company_id,
            dry_run=False,
            apply_confidence_threshold=0.8,
            days=7,
        )
    except Exception as exc:
        logger.error("run_auto_improvement_cycle: error company_id=%s: %s", company_id[:8], exc)
        await notify_pipeline_event(
            company_id=company_id,
            pipeline="internal/improvement_cycle",
            event_type="error",
            details={"error": str(exc)},
        )
        raise

    end_time = datetime.now(timezone.utc)
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    applied_count = sum(1 for s in result.get("improved_steps", []) if s.get("applied"))
    skipped_count = len(result.get("skipped_steps", []))

    # execution_logs に記録
    _record_execution_log(
        company_id=company_id,
        log_id=log_id,
        result=result,
        duration_ms=duration_ms,
    )

    # 完了通知
    await notify_pipeline_event(
        company_id=company_id,
        pipeline="internal/improvement_cycle",
        event_type="completed",
        details={
            "checked_steps": result.get("checked_steps", 0),
            "improvement_targets": result.get("improvement_targets", 0),
            "applied_count": applied_count,
            "skipped_count": skipped_count,
            "duration_ms": duration_ms,
        },
    )

    logger.info(
        "run_auto_improvement_cycle: done company_id=%s applied=%d skipped=%d duration=%dms",
        company_id[:8], applied_count, skipped_count, duration_ms,
    )
    return {**result, "log_id": log_id, "duration_ms": duration_ms}


def _collect_failing_examples(
    pipeline: str,
    step_name: str,
    company_id: str | None = None,
) -> list[dict]:
    """execution_logs から失敗事例を収集（最大10件）。

    overall_success=False のレコードから operations.steps を抽出する。
    DB接続失敗時は空リストを返す。

    Args:
        pipeline: パイプライン名（例: "construction/estimation"）
        step_name: ステップ名（例: "extract"）
        company_id: テナントID。指定された場合はそのテナントのみ対象とする

    Returns:
        list[dict]: 各要素は {"input": str, "output": str, "error": str, "created_at": str}
    """
    try:
        db = get_service_client()
        query = (
            db.table("execution_logs")
            .select("operations, created_at")
            .eq("overall_success", False)
            .order("created_at", desc=True)
            .limit(200)  # 最新200件の失敗からフィルタして最大10件を返す
        )
        if company_id is not None:
            query = query.eq("company_id", company_id)
        result = query.execute()
        examples: list[dict] = []
        for row in (result.data or []):
            ops = row.get("operations") or {}
            if ops.get("pipeline") != pipeline:
                continue
            row_created_at = row.get("created_at", "")
            for step in ops.get("steps", []):
                if step.get("step") != step_name:
                    continue
                examples.append({
                    "input": str(ops.get("input_data", ""))[:500],
                    "output": str(step.get("result", ""))[:500],
                    "error": str(step.get("error", step.get("failed_reason", "")))[:300],
                    "confidence": step.get("confidence"),
                    "created_at": row_created_at,
                })
                if len(examples) >= 10:
                    return examples
        return examples
    except Exception:
        logger.debug("_collect_failing_examples: DB error for %s/%s", pipeline, step_name)
        return []


def _read_current_prompt(pipeline: str, step_name: str) -> str:
    """llm/prompts/ 配下の .py ファイルからプロンプトを読む。

    パス解決順序:
    1. {_PROMPTS_DIR}/{pipeline_safe}_{step_safe}.py
    2. {_PROMPTS_DIR}/{pipeline_safe}.py（ステップ固有ファイルがない場合）
    3. {_PROMPTS_DIR}/{step_safe}.py（パイプライン固有ファイルがない場合）
    4. 上記いずれもなければ空文字列を返す

    pipeline_safe_name の生成規則:
    - "/" を "_" に置換
    - 先頭の "workers." "brain." "routers." などのパッケージプレフィックスを削除

    Args:
        pipeline: パイプライン名（例: "construction/estimation" や "workers/bpo/construction/estimation"）
        step_name: ステップ名（例: "extract"）

    Returns:
        プロンプトファイルの内容、またはファイルが存在しない場合は空文字列
    """
    # "/" を "_" に置換
    raw_pipeline_safe = pipeline.replace("/", "_")
    # 先頭のパッケージプレフィックスを除去（workers_, brain_, routers_ 等）
    _strip_prefixes = ("workers_bpo_", "workers_", "brain_", "routers_", "bpo_")
    pipeline_safe = raw_pipeline_safe
    for prefix in _strip_prefixes:
        if pipeline_safe.startswith(prefix):
            pipeline_safe = pipeline_safe[len(prefix):]
            break

    step_safe = step_name.replace("/", "_").replace(".", "_")

    candidates = [
        os.path.join(_PROMPTS_DIR, f"{pipeline_safe}_{step_safe}.py"),
        os.path.join(_PROMPTS_DIR, f"{pipeline_safe}.py"),
        os.path.join(_PROMPTS_DIR, f"{step_safe}.py"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except OSError as exc:
                logger.warning("_read_current_prompt: cannot read %s: %s", path, exc)
                return ""
    return ""


def _write_prompt_suggestion(
    pipeline: str,
    step_name: str,
    suggestion: str,
    dry_run: bool = True,
) -> None:
    """改善プロンプトを llm/prompts/suggestions/ に書き出す。

    dry_run=False の場合は実際のプロンプトファイル（llm/prompts/配下）も更新する。
    更新前のプロンプトはバックアップを作成する。

    Args:
        pipeline: パイプライン名（例: "construction/estimation"）
        step_name: ステップ名（例: "extract"）
        suggestion: 改善後のプロンプト文字列
        dry_run: False の場合のみ本番ファイルを更新
    """
    safe_pipeline = pipeline.replace("/", "_")
    safe_name = f"{safe_pipeline}_{step_name}"

    # suggestions/ にサジェストファイルを書く（dry_run に関わらず常に実行）
    suggestions_dir = os.path.join(_PROMPTS_DIR, "suggestions")
    os.makedirs(suggestions_dir, exist_ok=True)
    suggestion_path = os.path.join(suggestions_dir, f"{safe_name}.txt")
    with open(suggestion_path, "w", encoding="utf-8") as f:
        f.write(suggestion)
    logger.info("_write_prompt_suggestion: suggestion written to %s", suggestion_path)

    if dry_run:
        return

    # 本番プロンプトファイルの更新
    target_path = os.path.join(_PROMPTS_DIR, f"{safe_name}.py")
    if not os.path.exists(target_path):
        # 既存ファイルが .py でなく pipeline 単位の場合も確認
        alt_path = os.path.join(_PROMPTS_DIR, f"{safe_pipeline}.py")
        if os.path.exists(alt_path):
            target_path = alt_path

    if os.path.exists(target_path):
        # バックアップを作成
        backup_dir = os.path.join(_PROMPTS_DIR, "backup")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"{timestamp}_{safe_name}.py"
        backup_path = os.path.join(backup_dir, backup_name)
        try:
            with open(target_path, encoding="utf-8") as src:
                backup_content = src.read()
            with open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(backup_content)
            logger.info(
                "_write_prompt_suggestion: backup created at %s", backup_path
            )
        except OSError as exc:
            logger.error(
                "_write_prompt_suggestion: backup failed for %s: %s", target_path, exc
            )
            return

        # 本番ファイルを更新
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(suggestion)
            logger.info(
                "_write_prompt_suggestion: prompt updated at %s", target_path
            )
        except OSError as exc:
            logger.error(
                "_write_prompt_suggestion: write failed for %s: %s", target_path, exc
            )
    else:
        # ファイルが存在しない場合は新規作成
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(suggestion)
            logger.info(
                "_write_prompt_suggestion: new prompt file created at %s", target_path
            )
        except OSError as exc:
            logger.error(
                "_write_prompt_suggestion: create failed for %s: %s", target_path, exc
            )


def _estimate_suggestion_confidence(suggestion: str) -> float:
    """提案テキストから暫定的な信頼度を推定する。

    optimize_prompt は文字列を返すため、提案の長さとキーワード密度で信頼度を推定する。

    Returns:
        0.0〜1.0 の信頼度スコア
    """
    if not suggestion or len(suggestion.strip()) < 50:
        return 0.0
    # 長さベースのスコア（200文字以上で0.7、500文字以上で0.9）
    length = len(suggestion)
    if length >= 500:
        base_score = 0.9
    elif length >= 200:
        base_score = 0.7
    elif length >= 100:
        base_score = 0.5
    else:
        base_score = 0.3
    # 具体的な改善指示キーワードが含まれていれば+0.1
    improvement_keywords = ["改善", "修正", "追加", "明確化", "具体的", "フォーマット", "出力"]
    keyword_bonus = 0.1 if any(kw in suggestion for kw in improvement_keywords) else 0.0
    return min(base_score + keyword_bonus, 1.0)


def _get_last_improvement_applied_at(
    pipeline: str,
    step_name: str,
    company_id: str | None = None,
) -> datetime | None:
    """prompt_versions テーブルから最新の改善適用日時を取得する。

    improvement_applied_at カラムがある場合はそれを優先し、
    なければ prompt_versions の最新 created_at を代用する。
    DB接続失敗時は None を返す（スキップ不要として扱う）。

    Args:
        pipeline: パイプライン名
        step_name: ステップ名
        company_id: テナントID

    Returns:
        最新改善適用日時（aware datetime）、または None
    """
    try:
        db = get_service_client()
        query = (
            db.table("prompt_versions")
            .select("created_at, improvement_applied_at")
            .eq("pipeline", pipeline)
            .eq("step_name", step_name)
            .order("created_at", desc=True)
            .limit(1)
        )
        if company_id is not None:
            query = query.eq("company_id", company_id)
        else:
            query = query.is_("company_id", "null")
        result = query.execute()
        if not result or not result.data:
            return None
        row = result.data[0]

        # improvement_applied_at カラムが存在する場合は優先して返す
        raw_applied_at: str | None = row.get("improvement_applied_at")
        if raw_applied_at:
            dt = datetime.fromisoformat(raw_applied_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        # フォールバック: created_at を改善適用日として扱う
        raw_created_at: str | None = row.get("created_at")
        if raw_created_at:
            dt = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        return None
    except Exception:
        logger.debug(
            "_get_last_improvement_applied_at: DB error for %s/%s", pipeline, step_name
        )
        return None


def _update_improvement_applied_at(
    pipeline: str,
    step_name: str,
    company_id: str | None,
    applied_at: datetime,
) -> None:
    """prompt_versions テーブルの最新アクティブレコードの improvement_applied_at を更新する。

    improvement_applied_at カラムが存在しない場合は操作をスキップする（ログのみ）。
    DB接続失敗時は例外を握りつぶす（fire-and-forget）。

    Args:
        pipeline: パイプライン名
        step_name: ステップ名
        company_id: テナントID
        applied_at: 適用日時（aware datetime）
    """
    try:
        db = get_service_client()
        query = (
            db.table("prompt_versions")
            .update({"improvement_applied_at": applied_at.isoformat()})
            .eq("pipeline", pipeline)
            .eq("step_name", step_name)
            .eq("is_active", True)
        )
        if company_id is not None:
            query = query.eq("company_id", company_id)
        else:
            query = query.is_("company_id", "null")
        query.execute()
        logger.info(
            "_update_improvement_applied_at: updated %s/%s at %s",
            pipeline, step_name, applied_at.isoformat(),
        )
    except Exception as exc:
        logger.warning(
            "_update_improvement_applied_at: failed for %s/%s: %s", pipeline, step_name, exc
        )


def _record_skip_reason(
    pipeline: str,
    step_name: str,
    company_id: str | None,
    skip_reason: str,
) -> None:
    """execution_logs の最新レコードに improvement_skip_reason を記録する。

    DB接続失敗時は例外を握りつぶす（fire-and-forget）。

    Args:
        pipeline: パイプライン名
        step_name: ステップ名
        company_id: テナントID
        skip_reason: スキップ理由文字列
    """
    try:
        db = get_service_client()
        # 当該パイプライン・ステップの最新の execution_logs を特定して更新
        # operations JSONB内のフィールドでフィルタが難しいため、最新1件を取得してPython側で確認
        query = (
            db.table("execution_logs")
            .select("id, operations")
            .order("created_at", desc=True)
            .limit(10)
        )
        if company_id is not None:
            query = query.eq("company_id", company_id)
        result = query.execute()
        for row in (result.data or []):
            ops = row.get("operations") or {}
            if ops.get("pipeline") == pipeline:
                db.table("execution_logs").update(
                    {"improvement_skip_reason": skip_reason}
                ).eq("id", row["id"]).execute()
                logger.debug(
                    "_record_skip_reason: recorded skip_reason=%s for execution_id=%s",
                    skip_reason, row["id"],
                )
                break
    except Exception as exc:
        logger.debug("_record_skip_reason: failed for %s/%s: %s", pipeline, step_name, exc)


def _record_execution_log(
    company_id: str,
    log_id: str,
    result: dict[str, Any],
    duration_ms: int,
) -> None:
    """改善サイクルの実行結果を execution_logs に記録する。

    DB接続失敗時はログ出力のみで例外を握りつぶす（fire-and-forget）。
    """
    try:
        db = get_service_client()
        applied_count = sum(1 for s in result.get("improved_steps", []) if s.get("applied"))
        skipped_count = len(result.get("skipped_steps", []))
        db.table("execution_logs").insert({
            "id": log_id,
            "company_id": company_id,
            "overall_success": True,
            "operations": {
                "pipeline": "internal/improvement_cycle",
                "agent_name": "improvement_cycle",
                "checked_steps": result.get("checked_steps", 0),
                "improvement_targets": result.get("improvement_targets", 0),
                "applied_count": applied_count,
                "skipped_count": skipped_count,
                "improved_steps": result.get("improved_steps", []),
                "skipped_steps": result.get("skipped_steps", []),
                "duration_ms": duration_ms,
            },
        }).execute()
        logger.info(
            "_record_execution_log: recorded log_id=%s applied=%d skipped=%d",
            log_id, applied_count, skipped_count,
        )
    except Exception as exc:
        logger.warning("_record_execution_log: failed to record: %s", exc)

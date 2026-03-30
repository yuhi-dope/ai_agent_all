"""精度向上サイクル: モニタリング → 改善案生成 → (dry_run=False なら) ファイル更新。"""
from __future__ import annotations
import logging
from typing import Any

from brain.inference.accuracy_monitor import get_accuracy_report
from brain.inference.prompt_optimizer import optimize_prompt
from db.supabase import get_service_client

logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
    """
    精度向上サイクルを実行。
    dry_run=True の場合は改善案を返すだけでファイル変更なし（デフォルト）。
    """
    reports = await get_accuracy_report(company_id=company_id, days=days)
    targets = [r for r in reports if r.needs_improvement]

    improved: list[dict] = []
    total_cost = 0.0

    for report in targets:
        failing = _collect_failing_examples(report.pipeline, report.step_name)
        # 現在のプロンプトを取得（prompts/ から）
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
            }
            if not dry_run:
                _write_prompt_suggestion(report.pipeline, report.step_name, suggestion)
                entry["applied"] = True
                logger.info(f"Prompt updated: {report.pipeline}/{report.step_name}")
            improved.append(entry)

    return {
        "checked_steps": len(reports),
        "improvement_targets": len(targets),
        "improved_steps": improved,
        "dry_run": dry_run,
        "company_id": company_id,
    }


def _collect_failing_examples(pipeline: str, step_name: str) -> list[dict]:
    """execution_logs から失敗事例を収集（最大5件）。DB接続失敗時は空リスト。"""
    try:
        db = get_service_client()
        result = db.table("execution_logs").select("operations").execute()
        examples = []
        for row in (result.data or []):
            ops = row.get("operations") or {}
            if ops.get("pipeline") != pipeline:
                continue
            for step in ops.get("steps", []):
                if step.get("step") == step_name and float(step.get("confidence", 1.0)) < 0.8:
                    examples.append({
                        "input": str(ops.get("input_data", "")),
                        "output": str(step.get("result", "")),
                        "confidence": step.get("confidence"),
                    })
                    if len(examples) >= 5:
                        return examples
        return examples
    except Exception:
        return []


def _read_current_prompt(pipeline: str, step_name: str) -> str:
    """llm/prompts/ から現在のプロンプトを読む。なければデフォルト文字列を返す。"""
    import os
    safe_name = pipeline.replace("/", "_") + "_" + step_name
    path = f"llm/prompts/{safe_name}.txt"
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return f"# {pipeline} / {step_name}\n# (プロンプトファイル未作成)"


def _write_prompt_suggestion(pipeline: str, step_name: str, suggestion: str) -> None:
    """改善プロンプトを llm/prompts/suggestions/ に書き出す。"""
    import os
    os.makedirs("llm/prompts/suggestions", exist_ok=True)
    safe_name = pipeline.replace("/", "_") + "_" + step_name
    path = f"llm/prompts/suggestions/{safe_name}.txt"
    with open(path, "w") as f:
        f.write(suggestion)

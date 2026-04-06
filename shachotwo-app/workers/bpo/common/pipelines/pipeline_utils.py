"""
パイプライン共通ユーティリティ。

StepResult dataclass と、ステップ追跡・失敗ファクトリの生成ヘルパーを提供する。
新しいパイプラインはこのモジュールを import して _add_step / _fail の重複定義を避けること。
"""
import time
import logging
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from workers.micro.models import MicroAgentOutput

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

T = TypeVar("T")


@dataclass
class StepResult:
    """パイプライン内の単一ステップ実行結果。"""
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


# invoice_issue_pipeline.py が参照している別名（後方互換）
SharedStepResult = StepResult


def make_step_adder(steps: list[StepResult]) -> Callable:
    """
    steps リストへ StepResult を追加するクロージャを返す。

    使い方:
        record_step = make_step_adder(steps)
        record_step(1, "ocr", "document_ocr", micro_output)
    """
    def record(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms, warning=warn,
        )
        steps.append(sr)
        return sr

    return record


def make_fail_factory(
    steps: list[StepResult],
    pipeline_start_ms: int,
    result_class: type[T],
) -> Callable[[str], T]:
    """
    失敗時の PipelineResult を生成するクロージャを返す。

    使い方:
        emit_fail = make_fail_factory(steps, pipeline_start, MyPipelineResult)
        if not step_out.success:
            return emit_fail("step_name")
    """
    def emit(step_name: str) -> T:
        return result_class(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start_ms,
            failed_step=step_name,
        )

    return emit


def steps_cost(steps: list[StepResult]) -> float:
    """ステップリストの合計コスト（円）を返す。"""
    return sum(s.cost_yen for s in steps)


def steps_duration(steps: list[StepResult], pipeline_start_ms: int) -> int:
    """パイプライン開始時刻からの経過ミリ秒を返す。"""
    return int(time.time() * 1000) - pipeline_start_ms


def format_pipeline_summary(
    label: str,
    total_steps: int,
    success: bool,
    steps: list[StepResult],
    total_cost_yen: float,
    total_duration_ms: int,
    approval_required: bool = False,
    failed_step: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """
    パイプライン実行結果のサマリー文字列を生成する（approval_required フラグ付き版）。

    journal_entry_pipeline 等の to_report メソッドから呼ばれる。
    """
    lines = [
        f"{'OK' if success else 'NG'} {label}",
        f"  ステップ: {len(steps)}/{total_steps}",
        f"  コスト: Y{total_cost_yen:.2f}",
        f"  処理時間: {total_duration_ms}ms",
    ]
    if approval_required:
        lines.append("  承認待ち: あり")
    if extra_lines:
        lines.extend(extra_lines)
    if failed_step:
        lines.append(f"  失敗ステップ: {failed_step}")
    for s in steps:
        status = "OK" if s.success else "NG"
        warn = f" [{s.warning}]" if s.warning else ""
        lines.append(
            f"  Step {s.step_no} {status} {s.step_name}: "
            f"confidence={s.confidence:.2f}{warn}"
        )
    return "\n".join(lines)


def pipeline_summary(
    label: str,
    total_steps: int,
    steps: list[StepResult],
    total_cost_yen: float,
    total_duration_ms: int,
    failed_step: str | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    """
    パイプライン実行結果の共通サマリー文字列を生成する。

    Args:
        label: パイプライン表示名（例: "採用パイプライン"）
        total_steps: 総ステップ数
        steps: 実行済み StepResult リスト
        total_cost_yen: 総コスト（円）
        total_duration_ms: 総処理時間（ms）
        failed_step: 失敗したステップ名（成功時は None）
        extra_lines: 追加表示行（警告・承認フラグ等）
    """
    success = failed_step is None
    lines = [
        f"{'OK' if success else 'NG'} {label}",
        f"  ステップ: {len(steps)}/{total_steps}",
        f"  コスト: Y{total_cost_yen:.2f}",
        f"  処理時間: {total_duration_ms}ms",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    if failed_step:
        lines.append(f"  失敗ステップ: {failed_step}")
    for s in steps:
        status = "OK" if s.success else "NG"
        warn = f" [{s.warning}]" if s.warning else ""
        lines.append(
            f"  Step {s.step_no} {status} {s.step_name}: "
            f"confidence={s.confidence:.2f}{warn}"
        )
    return "\n".join(lines)

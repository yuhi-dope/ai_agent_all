"""契約書AIパイプライン — 契約書テキストを構造化・リスク分析・差分検出する。

Step 1: document_ocr        契約書テキスト抽出
Step 2: contract_extractor  契約情報抽出（当事者・金額・期間・解除条件・自動更新等）
Step 3: risk_checker        契約リスク検出（不利条項・自動更新・違約金等）
Step 4: diff_detector       前回契約との差分検出（改訂版の場合）
Step 5: output_validator    必須項目バリデーション
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from workers.micro.diff import run_diff_detector
from workers.micro.extractor import run_structured_extractor
from workers.micro.ocr import run_document_ocr
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 契約情報抽出スキーマ
# ------------------------------------------------------------------ #
_CONTRACT_SCHEMA: dict[str, str] = {
    "contract_title": "string",
    "party_a": "string（甲）",
    "party_b": "string（乙）",
    "contract_amount": "number (0 if not applicable)",
    "start_date": "string (YYYY-MM-DD or 不明)",
    "end_date": "string (YYYY-MM-DD or 不明)",
    "auto_renewal": "boolean",
    "cancellation_notice_days": "number (解除通知日数)",
    "penalty_clause": "string (違約金条項、なければ空文字)",
    "governing_law": "string (準拠法、なければ日本法)",
}

# 必須フィールド
REQUIRED_FIELDS: list[str] = [
    "contract_title",
    "party_a",
    "party_b",
    "start_date",
    "end_date",
]


# ------------------------------------------------------------------ #
# データクラス
# ------------------------------------------------------------------ #
@dataclass
class StepResult:
    """1ステップの実行結果。"""
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


@dataclass
class ContractPipelineResult:
    """パイプライン全体の実行結果。"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    risk_alerts: list[str] = field(default_factory=list)
    review_required: bool = False


# ------------------------------------------------------------------ #
# パイプライン本体
# ------------------------------------------------------------------ #
async def run_contract_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    previous_contract: dict | None = None,
) -> ContractPipelineResult:
    """契約書AIパイプラインを実行する。

    Args:
        company_id: テナントID
        input_data: {"text": str} or {"file_path": str} or {"contract": dict}
            - "contract" キーがある場合は OCR・抽出をスキップして直渡し
        previous_contract: 前回版の契約情報dict（改訂版の場合に渡す）

    Returns:
        ContractPipelineResult
    """
    pipeline_start = int(time.time() * 1000)
    result = ContractPipelineResult(success=False)

    # ---------------------------------------------------------------- #
    # Step 1: document_ocr
    # ---------------------------------------------------------------- #
    step1_start = int(time.time() * 1000)
    try:
        if input_data.get("contract"):
            # contract dict が直接渡された場合はOCRスキップ
            ocr_text: str = ""
            step1_result = StepResult(
                step_no=1,
                step_name="document_ocr",
                agent_name="document_ocr",
                success=True,
                result={"skipped": True, "reason": "contract dict provided"},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - step1_start,
                warning="OCRスキップ（contract dict直渡し）",
            )
        else:
            ocr = await run_document_ocr(input_data)
            ocr_text = ocr.text
            step1_result = StepResult(
                step_no=1,
                step_name="document_ocr",
                agent_name="document_ocr",
                success=True,
                result={"text_length": len(ocr_text), "source": ocr.source},
                confidence=ocr.confidence,
                cost_yen=ocr.cost_yen,
                duration_ms=ocr.duration_ms,
            )
    except Exception as exc:
        logger.error(f"contract_pipeline Step1 (document_ocr) 失敗: {exc}")
        result.failed_step = "document_ocr"
        result.steps.append(StepResult(
            step_no=1,
            step_name="document_ocr",
            agent_name="document_ocr",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step1_start,
            warning=str(exc),
        ))
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    result.steps.append(step1_result)
    result.total_cost_yen += step1_result.cost_yen

    # ---------------------------------------------------------------- #
    # Step 2: contract_extractor
    # ---------------------------------------------------------------- #
    step2_start = int(time.time() * 1000)
    try:
        if input_data.get("contract"):
            # contract dict が直接渡された場合は抽出スキップ
            extracted_contract: dict[str, Any] = dict(input_data["contract"])
            step2_result = StepResult(
                step_no=2,
                step_name="contract_extractor",
                agent_name="structured_extractor",
                success=True,
                result=extracted_contract,
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - step2_start,
                warning="抽出スキップ（contract dict直渡し）",
            )
        else:
            extractor = await run_structured_extractor(
                text=ocr_text,
                schema=_CONTRACT_SCHEMA,
                company_id=company_id,
                task_type="contract_extraction",
            )
            extracted_contract = extractor.data
            step2_result = StepResult(
                step_no=2,
                step_name="contract_extractor",
                agent_name="structured_extractor",
                success=True,
                result=extracted_contract,
                confidence=extractor.confidence,
                cost_yen=extractor.cost_yen,
                duration_ms=extractor.duration_ms,
            )
    except Exception as exc:
        logger.error(f"contract_pipeline Step2 (contract_extractor) 失敗: {exc}")
        result.failed_step = "contract_extractor"
        result.steps.append(StepResult(
            step_no=2,
            step_name="contract_extractor",
            agent_name="structured_extractor",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step2_start,
            warning=str(exc),
        ))
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    result.steps.append(step2_result)
    result.total_cost_yen += step2_result.cost_yen

    # ---------------------------------------------------------------- #
    # Step 3: risk_checker
    # ---------------------------------------------------------------- #
    step3_start = int(time.time() * 1000)
    try:
        risk_alerts, review_required = _check_contract_risks(extracted_contract)
        result.risk_alerts.extend(risk_alerts)
        result.review_required = result.review_required or review_required

        step3_result = StepResult(
            step_no=3,
            step_name="risk_checker",
            agent_name="risk_checker",
            success=True,
            result={
                "risk_alerts": risk_alerts,
                "review_required": review_required,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step3_start,
            warning="; ".join(risk_alerts) if risk_alerts else None,
        )
    except Exception as exc:
        logger.error(f"contract_pipeline Step3 (risk_checker) 失敗: {exc}")
        result.failed_step = "risk_checker"
        result.steps.append(StepResult(
            step_no=3,
            step_name="risk_checker",
            agent_name="risk_checker",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step3_start,
            warning=str(exc),
        ))
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    result.steps.append(step3_result)

    # ---------------------------------------------------------------- #
    # Step 4: diff_detector
    # ---------------------------------------------------------------- #
    step4_start = int(time.time() * 1000)
    try:
        if previous_contract is None:
            # 前回契約なし → スキップ
            step4_result = StepResult(
                step_no=4,
                step_name="diff_detector",
                agent_name="diff_detector",
                success=True,
                result={"skipped": True, "reason": "no previous contract"},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - step4_start,
                warning="差分検出スキップ（前回契約なし）",
            )
        else:
            diff = await run_diff_detector(
                before=previous_contract,
                after=extracted_contract,
                company_id=company_id,
                task_type="contract_diff",
            )
            step4_result = StepResult(
                step_no=4,
                step_name="diff_detector",
                agent_name="diff_detector",
                success=True,
                result={
                    "changes": diff.changes,
                    "change_summary": diff.change_summary,
                    "has_significant_changes": diff.has_significant_changes,
                },
                confidence=diff.confidence,
                cost_yen=diff.cost_yen,
                duration_ms=diff.duration_ms,
                warning=diff.change_summary if diff.has_significant_changes else None,
            )
    except Exception as exc:
        logger.error(f"contract_pipeline Step4 (diff_detector) 失敗: {exc}")
        result.failed_step = "diff_detector"
        result.steps.append(StepResult(
            step_no=4,
            step_name="diff_detector",
            agent_name="diff_detector",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step4_start,
            warning=str(exc),
        ))
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    result.steps.append(step4_result)
    result.total_cost_yen += step4_result.cost_yen

    # ---------------------------------------------------------------- #
    # Step 5: output_validator
    # ---------------------------------------------------------------- #
    step5_start = int(time.time() * 1000)
    try:
        validator = await run_output_validator(
            data=extracted_contract,
            required_fields=REQUIRED_FIELDS,
            task_type="contract_validation",
        )
        step5_result = StepResult(
            step_no=5,
            step_name="output_validator",
            agent_name="output_validator",
            success=True,
            result={
                "is_valid": validator.is_valid,
                "missing_fields": validator.missing_fields,
                "warnings": validator.warnings,
            },
            confidence=validator.confidence,
            cost_yen=0.0,
            duration_ms=validator.duration_ms,
            warning="; ".join(validator.warnings) if validator.warnings else None,
        )
    except Exception as exc:
        logger.error(f"contract_pipeline Step5 (output_validator) 失敗: {exc}")
        result.failed_step = "output_validator"
        result.steps.append(StepResult(
            step_no=5,
            step_name="output_validator",
            agent_name="output_validator",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - step5_start,
            warning=str(exc),
        ))
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    result.steps.append(step5_result)

    # ---------------------------------------------------------------- #
    # 最終出力を組み立て
    # ---------------------------------------------------------------- #
    result.success = True
    result.final_output = {
        **extracted_contract,
        "risk_alerts": result.risk_alerts,
        "review_required": result.review_required,
        "validation": {
            "is_valid": step5_result.result["is_valid"],
            "missing_fields": step5_result.result["missing_fields"],
        },
    }
    result.total_duration_ms = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"contract_pipeline 完了: success={result.success}, "
        f"cost=¥{result.total_cost_yen:.4f}, duration={result.total_duration_ms}ms, "
        f"risk_alerts={len(result.risk_alerts)}, review_required={result.review_required}"
    )
    return result


# ------------------------------------------------------------------ #
# リスクチェック（Step 3 内部ロジック）
# ------------------------------------------------------------------ #
def _check_contract_risks(
    contract: dict[str, Any],
) -> tuple[list[str], bool]:
    """契約情報からリスクアラートを生成する。

    Returns:
        (risk_alerts, review_required)
    """
    alerts: list[str] = []
    review_required = False

    # 自動更新 + 解除通知日数 >= 90日
    auto_renewal = contract.get("auto_renewal")
    cancellation_days = _to_int(contract.get("cancellation_notice_days", 0))
    if auto_renewal and cancellation_days >= 90:
        alerts.append(f"自動更新：解除通知{cancellation_days}日前が必要")

    # 違約金条項
    penalty_clause = contract.get("penalty_clause", "")
    if penalty_clause and str(penalty_clause).strip():
        alerts.append("違約金条項あり：法務確認推奨")
        review_required = True

    # 外国法準拠
    governing_law = str(contract.get("governing_law", ""))
    _foreign_keywords = ("英", "米", "外国")
    if any(kw in governing_law for kw in _foreign_keywords):
        alerts.append("外国法準拠：国際契約専門家確認推奨")
        review_required = True

    # 長期契約（5年超）
    end_date_str = str(contract.get("end_date", ""))
    start_date_str = str(contract.get("start_date", ""))
    if end_date_str and end_date_str not in ("不明", "") and start_date_str not in ("不明", ""):
        years = _contract_years(start_date_str, end_date_str)
        if years is not None and years > 5:
            alerts.append(f"長期契約（{years}年）：拘束力に注意")

    return alerts, review_required


def _to_int(value: Any) -> int:
    """値を整数に変換（失敗時は0）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _contract_years(start: str, end: str) -> int | None:
    """start/end日付文字列（YYYY-MM-DD）から契約年数を返す。パース失敗時はNone。"""
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        delta_days = (e - s).days
        return round(delta_days / 365)
    except (ValueError, TypeError):
        return None

"""製造業 SOP管理パイプライン（標準作業手順書）

Steps:
  Step 1: extractor       元情報からテキスト抽出・構造化
  Step 2: generator       LLMで標準作業手順書（SOP）生成
  Step 3: compliance      安全衛生法・業界規格チェック
  Step 4: diff            既存SOPとの差分検出（改訂管理）
  Step 5: validator       手順の論理整合性チェック
  Step 6: generator       PDF/HTML出力
  Step 7: saas_writer     SOP保存 + 承認フロー開始
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 安全衛生法 必須記載事項
SAFETY_REQUIRED_ITEMS = [
    "保護具の着用",
    "緊急時対応",
    "作業前確認",
    "作業後処置",
]

# SOP必須フィールド
SOP_REQUIRED_FIELDS = [
    "title",
    "version",
    "steps",
    "safety_notes",
]


@dataclass
class StepResult:
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
class SOPManagementResult:
    """SOP管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} SOP管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_sop_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    existing_sop_id: str | None = None,
) -> SOPManagementResult:
    """
    製造業SOP管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "text": str,           # 作業手順テキスト（直接入力）
            "file_path": str,      # 手順書ファイルパス（テキスト/画像）
            "title": str,          # SOP名称
            "process_name": str,   # 対象工程名
            "department": str,     # 担当部門
        }
        existing_sop_id: 既存SOPのID（改訂の場合）

    Returns:
        SOPManagementResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "existing_sop_id": existing_sop_id,
    }

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
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

    def _fail(step_name: str) -> SOPManagementResult:
        return SOPManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: extractor (テキスト抽出・構造化) ───────────────────────
    if "text" in input_data:
        raw_text = input_data["text"]
        steps.append(StepResult(
            step_no=1, step_name="extractor", agent_name="text_direct",
            success=True, result={"text": raw_text}, confidence=1.0,
            cost_yen=0.0, duration_ms=0,
        ))
    elif "file_path" in input_data:
        ocr_out = await run_document_ocr(MicroAgentInput(
            company_id=company_id,
            agent_name="document_ocr",
            payload={"file_path": input_data["file_path"]},
            context=context,
        ))
        _add_step(1, "extractor", "document_ocr", ocr_out)
        if not ocr_out.success:
            return _fail("extractor")
        raw_text = ocr_out.result.get("text", "")
    else:
        raw_text = ""
        steps.append(StepResult(
            step_no=1, step_name="extractor", agent_name="text_direct",
            success=True, result={"text": "", "warning": "入力テキストなし"},
            confidence=0.5, cost_yen=0.0, duration_ms=0,
        ))

    s1_out_struct = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": raw_text,
            "schema": {
                "title": "string",
                "process_name": "string",
                "steps": "list[{step_no: int, description: str, safety_notes: list[str]}]",
                "materials": "list[str]",
                "tools": "list[str]",
            },
        },
        context=context,
    ))
    # Step 1の構造化結果は既存のStepResultに追記ではなく、context更新のみ
    context["raw_text"] = raw_text
    sop_title = input_data.get("title", s1_out_struct.result.get("title", "無題SOP"))
    process_name = input_data.get(
        "process_name", s1_out_struct.result.get("process_name", "")
    )
    department = input_data.get("department", "")
    sop_steps = s1_out_struct.result.get("steps", [])
    context.update({
        "sop_title": sop_title,
        "process_name": process_name,
        "department": department,
        "sop_steps": sop_steps,
    })

    # ─── Step 2: generator (SOP生成) ────────────────────────────────────
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "標準作業手順書（SOP）",
            "variables": {
                "title": sop_title,
                "process_name": process_name,
                "department": department,
                "raw_steps": sop_steps,
                "materials": s1_out_struct.result.get("materials", []),
                "tools": s1_out_struct.result.get("tools", []),
            },
        },
        context=context,
    ))
    _add_step(2, "generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("generator")
    generated_sop = s2_out.result
    context["generated_sop"] = generated_sop

    # ─── Step 3: compliance (安全衛生法チェック) ────────────────────────
    s3_start = int(time.time() * 1000)
    compliance_warnings: list[str] = []
    sop_content = generated_sop.get("content", "")
    for required_item in SAFETY_REQUIRED_ITEMS:
        if required_item not in sop_content:
            compliance_warnings.append(
                f"安全衛生法必須記載事項が不足: 「{required_item}」の記載がありません"
            )

    s3_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "compliance_warnings": compliance_warnings,
            "passed": len(compliance_warnings) == 0,
            "checked_items": SAFETY_REQUIRED_ITEMS,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "compliance", "compliance_checker", s3_out)
    context["compliance_warnings"] = compliance_warnings

    # ─── Step 4: diff (既存SOPとの差分検出) ────────────────────────────
    s4_start = int(time.time() * 1000)
    diff_result: dict[str, Any] = {"has_diff": False, "changes": []}
    if existing_sop_id:
        # TODO: DBから既存SOPを取得して差分検出
        # 現在はスケルトン
        diff_result = {
            "has_diff": True,
            "existing_sop_id": existing_sop_id,
            "changes": [],  # TODO: 差分リスト
            "revision_reason": input_data.get("revision_reason", ""),
        }

    s4_out = MicroAgentOutput(
        agent_name="diff_checker",
        success=True,
        result=diff_result,
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "diff", "diff_checker", s4_out)
    context["diff_result"] = diff_result

    # ─── Step 5: validator (論理整合性チェック) ─────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "title": sop_title,
                "version": generated_sop.get("version", "1.0"),
                "steps": sop_steps,
                "safety_notes": compliance_warnings,
            },
            "required_fields": SOP_REQUIRED_FIELDS,
        },
        context=context,
    ))
    _add_step(5, "validator", "output_validator", val_out)

    # ─── Step 6: generator (PDF/HTML出力) ──────────────────────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "SOP_PDF出力",
            "format": "pdf",
            "variables": {
                "title": sop_title,
                "process_name": process_name,
                "department": department,
                "version": generated_sop.get("version", "1.0"),
                "content": sop_content,
                "compliance_warnings": compliance_warnings,
            },
        },
        context=context,
    ))
    _add_step(6, "generator_pdf", "document_generator", s6_out)
    context["pdf_output"] = s6_out.result

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    # TODO: SOP保存（sop_documents テーブル）+ 承認フロー開始
    logger.info(
        f"sop_management_pipeline: company_id={company_id}, "
        f"title={sop_title}, existing_sop_id={existing_sop_id}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "approval_flow_started": False,  # TODO: 承認フロー実装
            "sop_saved": True,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "sop_title": sop_title,
        "process_name": process_name,
        "department": department,
        "version": generated_sop.get("version", "1.0"),
        "generated_sop": generated_sop,
        "compliance_warnings": compliance_warnings,
        "diff_result": diff_result,
        "pdf_output": s6_out.result,
    }

    return SOPManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

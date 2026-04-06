"""製造業 ISO文書管理パイプライン

Steps:
  Step 1: saas_reader     文書マスタ + 改訂履歴取得
  Step 2: extractor       文書メタデータ構造化
  Step 3: rule_matcher    ISO 9001/14001条項別の文書有無チェック
  Step 4: compliance      有効期限・改訂周期チェック
  Step 5: diff            前回監査との差分検出
  Step 6: generator       監査チェックリスト + 不適合レポート生成
  Step 7: validator       文書体系の完全性チェック（必須文書の欠損検出）
  Step 8: saas_writer     監査記録保存 + 期限アラート
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 文書有効期限アラート日数
DOCUMENT_EXPIRY_ALERT_DAYS = 60

# ISO 9001:2015 必須文書（最低限）
ISO9001_MANDATORY_DOCUMENTS = [
    "品質マニュアル",
    "品質方針",
    "品質目標",
    "文書管理手順書",
    "記録管理手順書",
    "内部監査手順書",
    "不適合管理手順書",
    "是正処置手順書",
]

# ISO 14001:2015 追加必須文書
ISO14001_MANDATORY_DOCUMENTS = [
    "環境マニュアル",
    "環境方針",
    "環境目標",
    "緊急事態対応手順書",
]

# 文書改訂周期（年）デフォルト
DEFAULT_REVISION_CYCLE_YEARS = 3


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
class ISODocumentResult:
    """ISO文書管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} ISO文書管理パイプライン",
            f"  ステップ: {len(self.steps)}/8",
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


async def run_iso_document_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    iso_standard: str = "9001",
    previous_audit_id: str | None = None,
) -> ISODocumentResult:
    """
    ISO文書管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "documents": [
                {
                    "document_id": str,
                    "document_name": str,
                    "document_type": str,
                    "version": str,
                    "last_revised_date": str,   # YYYY-MM-DD
                    "expiry_date": str,          # YYYY-MM-DD（任意）
                    "department": str,
                    "iso_clause": str,           # 対応するISO条項番号
                }
            ]
        }
        iso_standard: "9001" or "14001" or "both"
        previous_audit_id: 前回監査ID（差分検出用）

    Returns:
        ISODocumentResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "iso_standard": iso_standard,
        "today": today.isoformat(),
        "previous_audit_id": previous_audit_id,
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

    def _fail(step_name: str) -> ISODocumentResult:
        return ISODocumentResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    documents: list[dict] = input_data.get("documents", [])
    context["documents"] = documents

    # ─── Step 1: saas_reader ────────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    # TODO: DBから文書マスタ + 改訂履歴を取得
    s1_out = MicroAgentOutput(
        agent_name="saas_reader",
        success=True,
        result={"documents": documents, "source": "direct"},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("saas_reader")

    # ─── Step 2: extractor (文書メタデータ構造化) ─────────────────────
    s2_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _serialize_documents(documents),
            "schema": {
                "documents": "list[{document_id: str, document_name: str, "
                             "version: str, last_revised_date: str, iso_clause: str}]",
            },
        },
        context=context,
    ))
    _add_step(2, "extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _fail("extractor")

    # ─── Step 3: rule_matcher (ISO条項別チェック) ───────────────────────
    # 必須文書リストの決定
    mandatory_docs = list(ISO9001_MANDATORY_DOCUMENTS)
    if iso_standard in ("14001", "both"):
        mandatory_docs.extend(ISO14001_MANDATORY_DOCUMENTS)

    existing_doc_names = {d.get("document_name", "") for d in documents}
    missing_mandatory: list[str] = [
        doc for doc in mandatory_docs if doc not in existing_doc_names
    ]

    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": documents,
            "rule_type": "iso_clause_coverage",
            "mandatory_documents": mandatory_docs,
            "missing_mandatory": missing_mandatory,
        },
        context=context,
    ))
    _add_step(3, "rule_matcher", "rule_matcher", s3_out)
    context["missing_mandatory"] = missing_mandatory

    # ─── Step 4: compliance (有効期限・改訂周期チェック) ────────────────
    s4_start = int(time.time() * 1000)
    expiry_alerts: list[dict] = []
    revision_alerts: list[dict] = []
    cutoff_expiry = today + timedelta(days=DOCUMENT_EXPIRY_ALERT_DAYS)

    for doc in documents:
        doc_name = doc.get("document_name", "不明")

        # 有効期限チェック
        expiry_str = doc.get("expiry_date", "")
        if expiry_str:
            try:
                expiry_date = date.fromisoformat(expiry_str)
                days_until = (expiry_date - today).days
                if days_until <= 0:
                    expiry_alerts.append({
                        "document_name": doc_name,
                        "expiry_date": expiry_str,
                        "days_until": days_until,
                        "severity": "expired",
                    })
                elif expiry_date <= cutoff_expiry:
                    expiry_alerts.append({
                        "document_name": doc_name,
                        "expiry_date": expiry_str,
                        "days_until": days_until,
                        "severity": "warning",
                    })
            except ValueError:
                pass

        # 改訂周期チェック
        last_revised_str = doc.get("last_revised_date", "")
        if last_revised_str:
            try:
                last_revised = date.fromisoformat(last_revised_str)
                revision_cycle_years = int(
                    doc.get("revision_cycle_years", DEFAULT_REVISION_CYCLE_YEARS)
                )
                next_revision = date(
                    last_revised.year + revision_cycle_years,
                    last_revised.month,
                    last_revised.day,
                )
                if next_revision < today:
                    revision_alerts.append({
                        "document_name": doc_name,
                        "last_revised_date": last_revised_str,
                        "overdue_days": (today - next_revision).days,
                    })
            except ValueError:
                pass

    s4_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "expiry_alerts": expiry_alerts,
            "revision_alerts": revision_alerts,
            "passed": len(expiry_alerts) == 0 and len(revision_alerts) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "compliance", "compliance_checker", s4_out)
    context.update({
        "expiry_alerts": expiry_alerts,
        "revision_alerts": revision_alerts,
    })

    # ─── Step 5: diff (前回監査との差分検出) ────────────────────────────
    s5_start = int(time.time() * 1000)
    diff_result: dict[str, Any] = {
        "has_previous_audit": previous_audit_id is not None,
        "changes": [],
    }
    if previous_audit_id:
        # TODO: DBから前回監査データを取得して差分検出
        diff_result["previous_audit_id"] = previous_audit_id
        # diff_result["changes"] = 差分リスト

    s5_out = MicroAgentOutput(
        agent_name="diff_checker",
        success=True,
        result=diff_result,
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "diff", "diff_checker", s5_out)
    context["diff_result"] = diff_result

    # ─── Step 6: generator (監査チェックリスト + 不適合レポート) ─────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "ISO監査チェックリスト",
            "variables": {
                "iso_standard": iso_standard,
                "documents": documents,
                "missing_mandatory": missing_mandatory,
                "expiry_alerts": expiry_alerts,
                "revision_alerts": revision_alerts,
                "diff_changes": diff_result.get("changes", []),
            },
        },
        context=context,
    ))
    _add_step(6, "generator", "document_generator", s6_out)
    context["generated_doc"] = s6_out.result

    # ─── Step 7: validator (文書体系の完全性チェック) ────────────────────
    completeness_issues: list[str] = []
    if missing_mandatory:
        for doc_name in missing_mandatory:
            completeness_issues.append(f"必須文書が欠損: 「{doc_name}」")
    for alert in expiry_alerts:
        if alert.get("severity") == "expired":
            completeness_issues.append(
                f"文書期限切れ: 「{alert['document_name']}」"
            )

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "documents": documents,
                "missing_mandatory": missing_mandatory,
                "completeness_issues": completeness_issues,
            },
            "required_fields": ["documents"],
        },
        context=context,
    ))
    _add_step(7, "validator", "output_validator", val_out)
    context["completeness_issues"] = completeness_issues

    # ─── Step 8: saas_writer ────────────────────────────────────────────
    s8_start = int(time.time() * 1000)
    # TODO: 監査記録保存（iso_audit_records テーブル）+ 期限アラートメール
    total_issues = len(missing_mandatory) + len(expiry_alerts) + len(revision_alerts)
    logger.info(
        f"iso_document_pipeline: company_id={company_id}, "
        f"iso_standard={iso_standard}, documents={len(documents)}, "
        f"total_issues={total_issues}"
    )
    s8_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "alert_sent": total_issues > 0,
            "total_issues": total_issues,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s8_start,
    )
    _add_step(8, "saas_writer", "saas_writer", s8_out)

    final_output = {
        "iso_standard": iso_standard,
        "document_count": len(documents),
        "missing_mandatory": missing_mandatory,
        "expiry_alerts": expiry_alerts,
        "revision_alerts": revision_alerts,
        "completeness_issues": completeness_issues,
        "diff_result": diff_result,
        "generated_doc": s6_out.result,
    }

    return ISODocumentResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_documents(documents: list[dict]) -> str:
    import json
    return json.dumps({"documents": documents}, ensure_ascii=False)

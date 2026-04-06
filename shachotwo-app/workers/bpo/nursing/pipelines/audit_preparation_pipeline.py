"""介護・福祉業 監査・実地指導準備パイプライン

Steps:
  Step 1: extractor          事業所情報・書類保有状況データ構造化
  Step 2: checklist_generator 実地指導チェックリスト生成（サービス種別×法令要件）
  Step 3: document_scanner    書類欠損検出（必須書類の有無・有効期限確認）
  Step 4: rule_matcher        法令・通知との照合（基準省令・運営基準等）
  Step 5: compliance_checker  指摘事項予測（過去の指摘パターンとの照合）
  Step 6: validator           出力バリデーション（チェックリスト完全性確認）
  Step 7: saas_writer         execution_logs保存 + 準備状況レポート出力
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# サービス種別ごとの必須書類一覧
REQUIRED_DOCUMENTS: dict[str, list[str]] = {
    "通所介護": [
        "指定申請書",
        "運営規程",
        "重要事項説明書",
        "利用者との契約書",
        "ケアプラン（居宅サービス計画書）",
        "通所介護計画書",
        "サービス提供記録（利用者署名付き）",
        "事故報告書（発生時）",
        "苦情対応記録",
        "会議録（サービス担当者会議）",
        "職員名簿（資格証明書含む）",
        "勤務体制一覧表",
        "処遇改善加算の計画書・実績報告書",
        "損害賠償保険加入証書",
    ],
    "訪問介護": [
        "指定申請書",
        "運営規程",
        "重要事項説明書",
        "利用者との契約書",
        "居宅サービス計画書",
        "訪問介護計画書",
        "サービス提供記録（利用者/家族署名付き）",
        "アセスメント表",
        "事故・ヒヤリハット報告書",
        "苦情対応記録",
        "職員名簿（訪問介護員資格証明書含む）",
        "サービス提供責任者の記録",
        "処遇改善加算の計画書・実績報告書",
    ],
    "特養": [
        "指定申請書",
        "運営規程",
        "重要事項説明書",
        "入居契約書",
        "施設サービス計画書",
        "アセスメント記録",
        "ケアカンファレンス記録",
        "事故・ヒヤリハット報告書",
        "苦情対応記録",
        "職員名簿（資格証明書含む）",
        "勤務体制一覧表（夜勤体制含む）",
        "医療連携記録",
        "処遇改善加算の計画書・実績報告書",
        "身体拘束廃止委員会記録",
    ],
}

# 実地指導でよく指摘される事項（スケルトン用）
COMMON_FINDINGS: list[str] = [
    "サービス提供記録の利用者署名漏れ",
    "ケアプランとサービス内容の不整合",
    "処遇改善加算の要件書類の不備",
    "運営規程の最新版未更新",
    "重要事項説明書の記載内容の不備",
    "事故報告書の行政への報告漏れ",
    "夜勤体制加算の算定要件不備",
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
class AuditPreparationResult:
    """監査・実地指導準備パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 監査・実地指導準備パイプライン",
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


async def run_audit_preparation_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> AuditPreparationResult:
    """
    監査・実地指導準備パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "facility_name": str,
            "service_type": str,         # "通所介護" / "訪問介護" / "特養" 等
            "inspection_date": str,      # YYYY-MM-DD（予定日）
            "existing_documents": list[str],  # 保有済み書類名のリスト
            "last_inspection_date": str,  # 前回実地指導日（任意）
            "last_findings": list[str],   # 前回の指摘事項（任意）
        }

    Returns:
        AuditPreparationResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {"company_id": company_id}

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

    def _fail(step_name: str) -> AuditPreparationResult:
        return AuditPreparationResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: extractor ──────────────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": _serialize_facility_info(input_data),
            "schema": {
                "facility_name": "string",
                "service_type": "string",
                "inspection_date": "string",
                "existing_documents": "list[string]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    facility_name = input_data.get("facility_name", s1_out.result.get("facility_name", ""))
    service_type = input_data.get("service_type", s1_out.result.get("service_type", "通所介護"))
    existing_docs = input_data.get("existing_documents", s1_out.result.get("existing_documents", []))
    context.update({
        "facility_name": facility_name,
        "service_type": service_type,
        "existing_documents": existing_docs,
    })

    # ─── Step 2: checklist_generator ─────────────────────────────────────
    required_docs = REQUIRED_DOCUMENTS.get(service_type, [])
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "実地指導準備チェックリスト",
            "variables": {
                "facility_name": facility_name,
                "service_type": service_type,
                "inspection_date": input_data.get("inspection_date", ""),
                "required_documents": required_docs,
                "existing_documents": existing_docs,
            },
        },
        context=context,
    ))
    _add_step(2, "checklist_generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("checklist_generator")
    checklist = s2_out.result
    context["checklist"] = checklist

    # ─── Step 3: document_scanner（書類欠損検出） ────────────────────────
    s3_start = int(time.time() * 1000)
    missing_documents: list[str] = []
    for doc in required_docs:
        if doc not in existing_docs:
            missing_documents.append(doc)
    s3_out = MicroAgentOutput(
        agent_name="document_scanner",
        success=True,
        result={
            "missing_documents": missing_documents,
            "total_required": len(required_docs),
            "total_existing": len([d for d in required_docs if d in existing_docs]),
            "completeness_rate": (
                (len(required_docs) - len(missing_documents)) / len(required_docs)
                if required_docs else 1.0
            ),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "document_scanner", "document_scanner", s3_out)
    context["missing_documents"] = missing_documents

    # ─── Step 4: rule_matcher（法令・通知との照合） ────────────────────
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": existing_docs,
            "rule_type": "care_facility_compliance",
            "service_type": service_type,
            "required_docs": required_docs,
        },
        context=context,
    ))
    _add_step(4, "rule_matcher", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("rule_matcher")
    compliance_issues = s4_out.result.get("unmatched", [])
    context["compliance_issues"] = compliance_issues

    # ─── Step 5: compliance_checker（指摘事項予測） ─────────────────────
    s5_start = int(time.time() * 1000)
    predicted_findings: list[str] = []
    last_findings = input_data.get("last_findings", [])
    # 前回指摘事項の再発リスク
    for finding in last_findings:
        predicted_findings.append(f"[前回指摘の再確認] {finding}")
    # 書類欠損からの指摘予測
    for doc in missing_documents:
        predicted_findings.append(f"[書類未整備] {doc} が見つかりません")
    s5_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "predicted_findings": predicted_findings,
            "risk_level": "HIGH" if len(predicted_findings) > 3 else "MEDIUM" if predicted_findings else "LOW",
        },
        confidence=0.85,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["predicted_findings"] = predicted_findings

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": checklist,
            "required_fields": ["content"],
            "missing_documents_count": len(missing_documents),
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    completeness_rate = s3_out.result.get("completeness_rate", 0.0)
    # TODO: 準備状況レポート出力・execution_logs保存
    logger.info(
        f"audit_preparation_pipeline: company_id={company_id}, "
        f"facility_name={facility_name}, "
        f"service_type={service_type}, "
        f"missing_docs={len(missing_documents)}, "
        f"completeness={completeness_rate:.0%}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "report_generated": False,  # TODO: レポート生成実装
            "missing_documents_count": len(missing_documents),
            "completeness_rate": completeness_rate,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "facility_name": facility_name,
        "service_type": service_type,
        "checklist": checklist,
        "missing_documents": missing_documents,
        "compliance_issues": compliance_issues,
        "predicted_findings": predicted_findings,
        "completeness_rate": completeness_rate,
    }

    return AuditPreparationResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_facility_info(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    import json
    return json.dumps(input_data, ensure_ascii=False)

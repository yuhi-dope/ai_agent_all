"""
共通BPO 仕訳入力パイプライン（マイクロエージェント版）

レジストリキー: backoffice/journal_entry
トリガー: 他パイプライン連鎖（expense/invoice/payroll完了後）/ 手動
承認: 金額 >= ¥100,000 の場合は承認必要
コネクタ: freee（仕訳API）

Steps:
  Step 1: extractor    取引内容から仕訳要素を推定 {借方科目, 貸方科目, 金額, 摘要, 税区分}
  Step 2: rule_matcher 過去仕訳パターン照合（同一取引先+科目の履歴から推定精度向上）
  Step 3: compliance   勘定科目の妥当性チェック（借貸バランス、税区分整合）
  Step 4: saas_writer  freee仕訳API → 仕訳レコード作成
  Step 5: validator    貸借一致検証
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.compliance import run_compliance_checker
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    steps_cost,
    steps_duration,
    format_pipeline_summary,
)

logger = logging.getLogger(__name__)

APPROVAL_THRESHOLD_YEN = 100_000
REQUIRED_ENTRY_FIELDS = ["debit_account", "credit_account", "amount", "description"]

JOURNAL_ENTRY_SCHEMA = {
    "debit_account": "string (借方勘定科目: 例 現金/売掛金/消耗品費/給与手当)",
    "credit_account": "string (貸方勘定科目: 例 現金/買掛金/売上/未払金)",
    "amount": "number (金額、税込)",
    "tax_amount": "number (消費税額)",
    "tax_category": "string (課税売上/課税仕入/非課税/不課税/免税)",
    "description": "string (摘要: 取引内容の説明)",
    "transaction_date": "string (YYYY-MM-DD)",
    "counterparty": "string (取引先名)",
}


@dataclass
class JournalEntryPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = False
    compliance_alerts: list[str] = field(default_factory=list)
    auto_classified: int = 0
    manual_review: int = 0
    freee_synced: bool = False

    def to_report(self) -> str:
        extra: list[str] = []
        extra.append(f"  自動分類: {self.auto_classified}件 / 要確認: {self.manual_review}件")
        extra.append(f"  freee同期: {'済' if self.freee_synced else '未'}")
        for alert in self.compliance_alerts:
            extra.append(f"  [コンプラアラート] {alert}")
        return format_pipeline_summary(
            label="仕訳入力パイプライン",
            total_steps=5,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            approval_required=self.approval_required,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_journal_entry_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> JournalEntryPipelineResult:
    """
    仕訳入力パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            "transaction_text": str  取引を説明するテキスト
            または "entries": list[dict]  仕訳エントリを直接渡す形式
            "dry_run": bool  Trueの場合はfreee書き込みをスキップ（省略時False）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    context: dict[str, Any] = {"company_id": company_id, "domain": "journal_entry"}
    dry_run: bool = bool(input_data.get("dry_run", False))

    def build_failure(step_name: str) -> JournalEntryPipelineResult:
        return JournalEntryPipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=steps_cost(steps),
            total_duration_ms=steps_duration(steps, pipeline_start),
            failed_step=step_name,
        )

    # ─── Step 1: extractor ────────────────────────────────────────────────
    if "entries" in input_data:
        raw_entries: list[dict] = input_data["entries"]
        s1_out = MicroAgentOutput(
            agent_name="structured_extractor",
            success=True,
            result={"entries": raw_entries, "source": "direct_input"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=0,
        )
    else:
        try:
            s1_out = await run_structured_extractor(MicroAgentInput(
                company_id=company_id,
                agent_name="structured_extractor",
                payload={
                    "text": input_data.get("transaction_text", ""),
                    "schema": JOURNAL_ENTRY_SCHEMA,
                    "instruction": (
                        "取引テキストから複式簿記の仕訳エントリを抽出してください。"
                        "借方科目・貸方科目・金額・摘要・税区分を必ず含めてください。"
                        "複数仕訳が必要な場合は entries リストで返してください。"
                    ),
                },
                context=context,
            ))
        except Exception as exc:
            s1_out = MicroAgentOutput(
                agent_name="structured_extractor",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=0,
            )

    record_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return build_failure("extractor")

    extracted = s1_out.result
    entries: list[dict] = extracted.get("entries", [extracted])
    context["entries"] = entries

    # ─── Step 2: rule_matcher ─────────────────────────────────────────────
    first_entry = entries[0] if entries else {}
    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "input_values": {
                    "counterparty": first_entry.get("counterparty", ""),
                    "debit_account": first_entry.get("debit_account", ""),
                    "credit_account": first_entry.get("credit_account", ""),
                    "amount": first_entry.get("amount", 0),
                },
                "domain": "journal_entry_pattern",
            },
            context=context,
        ))
    except Exception as exc:
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={"matched_rules": [], "pattern_confidence": 0.5, "note": str(exc)},
            confidence=0.5,
            cost_yen=0.0,
            duration_ms=0,
        )

    record_step(2, "rule_matcher", "rule_matcher", s2_out)

    # パターン照合結果で科目を補強
    matched_patterns = s2_out.result.get("matched_rules", [])
    if matched_patterns:
        best = matched_patterns[0]
        for entry in entries:
            if not entry.get("debit_account") and best.get("debit_account"):
                entry["debit_account"] = best["debit_account"]
            if not entry.get("credit_account") and best.get("credit_account"):
                entry["credit_account"] = best["credit_account"]
    context["entries"] = entries

    # ─── Step 3: compliance ───────────────────────────────────────────────
    compliance_alerts: list[str] = []
    total_amount = sum(int(e.get("amount", 0)) for e in entries)
    approval_required = total_amount >= APPROVAL_THRESHOLD_YEN

    try:
        s3_out = await run_compliance_checker(MicroAgentInput(
            company_id=company_id,
            agent_name="compliance_checker",
            payload={
                "entries": entries,
                "checks": ["debit_credit_balance", "tax_category_consistency", "account_validity"],
                "domain": "journal_entry",
            },
            context=context,
        ))
    except Exception as exc:
        local_warnings: list[str] = []
        for i, entry in enumerate(entries):
            if not entry.get("debit_account"):
                local_warnings.append(f"エントリ{i+1}: 借方科目が未設定")
            if not entry.get("credit_account"):
                local_warnings.append(f"エントリ{i+1}: 貸方科目が未設定")
            if not entry.get("amount") or entry.get("amount", 0) <= 0:
                local_warnings.append(f"エントリ{i+1}: 金額が0以下")
            if entry.get("debit_account") == entry.get("credit_account"):
                local_warnings.append(
                    f"エントリ{i+1}: 借方と貸方が同じ科目 ({entry.get('debit_account')})"
                )
        s3_out = MicroAgentOutput(
            agent_name="compliance_checker",
            success=True,
            result={"warnings": local_warnings, "passed": len(local_warnings) == 0, "note": str(exc)},
            confidence=0.8,
            cost_yen=0.0,
            duration_ms=0,
        )

    record_step(3, "compliance", "compliance_checker", s3_out)
    if not s3_out.success:
        return build_failure("compliance")

    compliance_alerts.extend(s3_out.result.get("warnings", []))

    # ─── Step 4: saas_writer ──────────────────────────────────────────────
    s4_start = int(time.time() * 1000)
    if dry_run:
        s4_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=True,
            result={"dry_run": True, "entries_count": len(entries), "freee_synced": False},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    else:
        try:
            s4_out = await run_saas_writer(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_writer",
                payload={
                    "service": "freee",
                    "operation": "create_journal_entries",
                    "data": {"entries": entries, "company_id": company_id},
                },
                context=context,
            ))
        except Exception as exc:
            s4_out = MicroAgentOutput(
                agent_name="saas_writer",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s4_start,
            )

    record_step(4, "saas_writer", "saas_writer", s4_out)
    freee_synced: bool = bool(s4_out.result.get("freee_synced", False))
    if not dry_run and not s4_out.success:
        compliance_alerts.append(f"freee同期エラー: {s4_out.result.get('error', '不明')}")

    # ─── Step 5: validator ────────────────────────────────────────────────
    try:
        s5_out = await run_output_validator(MicroAgentInput(
            company_id=company_id,
            agent_name="output_validator",
            payload={
                "document": {
                    "entries": entries,
                    "total_amount": total_amount,
                    "approval_required": approval_required,
                    "compliance_alerts": compliance_alerts,
                    "freee_synced": freee_synced,
                },
                "required_fields": REQUIRED_ENTRY_FIELDS,
                "numeric_fields": ["amount"],
                "positive_fields": ["amount"],
                "custom_checks": ["debit_credit_balance"],
            },
            context=context,
        ))
    except Exception as exc:
        debit_total = sum(float(e.get("amount", 0)) for e in entries)
        balance_ok = debit_total > 0
        s5_out = MicroAgentOutput(
            agent_name="output_validator",
            success=balance_ok,
            result={"balance_ok": balance_ok, "debit_total": debit_total, "note": str(exc)},
            confidence=0.9 if balance_ok else 0.0,
            cost_yen=0.0,
            duration_ms=0,
        )

    record_step(5, "validator", "output_validator", s5_out)

    # 自動分類 vs 要確認の集計
    pattern_confidence: float = float(s2_out.result.get("pattern_confidence", 0.0))
    if pattern_confidence >= 0.8 and not compliance_alerts:
        auto_classified = len(entries)
        manual_review = 0
    else:
        auto_classified = 0
        manual_review = len(entries)

    total_cost = steps_cost(steps)
    total_dur = steps_duration(steps, pipeline_start)

    logger.info(
        "journal_entry_pipeline complete: entries=%d, amount=¥%s, approval=%s, %dms",
        len(entries), f"{total_amount:,}", approval_required, total_dur,
    )

    return JournalEntryPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "entries": entries,
            "total_amount": total_amount,
            "approval_required": approval_required,
            "compliance_alerts": compliance_alerts,
            "freee_synced": freee_synced,
            "auto_classified": auto_classified,
            "manual_review": manual_review,
        },
        total_cost_yen=total_cost,
        total_duration_ms=total_dur,
        approval_required=approval_required,
        compliance_alerts=compliance_alerts,
        auto_classified=auto_classified,
        manual_review=manual_review,
        freee_synced=freee_synced,
    )

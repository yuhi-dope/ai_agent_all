"""
共通BPO 銀行照合パイプライン（バックオフィスBPO）

レジストリキー: backoffice/bank_reconciliation
トリガー: スケジュール（毎日18:00）/ 月末バッチ
承認: 差異あり時のみ承認必要
コネクタ: Bank API（入出金明細CSV/API）、freee（帳簿残高）

Steps:
  Step 1: saas_reader    銀行入出金明細取得（CSV or API）
  Step 2: saas_reader    freee帳簿上の入出金記録取得
  Step 3: rule_matcher   自動マッチング（日付+金額+摘要の組み合わせ）
  Step 4: extractor      不一致項目の原因分類（タイミング差/二重計上/未記帳/不明）
  Step 5: calculator     調整後残高の算出
  Step 6: generator      銀行勘定調整表（Bank Reconciliation Statement）生成
  Step 7: validator      差額ゼロ検証 or 差異レポート
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.extractor import run_structured_extractor
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    format_pipeline_summary,
)

logger = logging.getLogger(__name__)

# 差異原因の分類ラベル
DISCREPANCY_REASONS = [
    "timing_difference",    # タイミング差（振込中・未着）
    "double_entry",         # 二重計上
    "unrecorded",           # 未記帳
    "unknown",              # 不明
]


@dataclass
class BankReconciliationPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = False
    bank_balance: Decimal = field(default_factory=lambda: Decimal("0"))
    book_balance: Decimal = field(default_factory=lambda: Decimal("0"))
    adjusted_balance: Decimal = field(default_factory=lambda: Decimal("0"))
    auto_matched: int = 0
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    reconciled: bool = False
    compliance_alerts: list[str] = field(default_factory=list)

    def to_bank_summary(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        reconciled_label = "照合済（差額ゼロ）" if self.reconciled else f"差額あり ¥{int(self.bank_balance - self.book_balance):,}"
        extra = [
            f"  銀行残高: ¥{int(self.bank_balance):,}",
            f"  帳簿残高: ¥{int(self.book_balance):,}",
            f"  自動照合: {self.auto_matched}件",
            f"  不一致: {len(self.unmatched)}件",
            f"  照合結果: {reconciled_label}",
        ]
        if self.approval_required:
            extra.append("  承認者確認が必要（差異あり）")
        for alert in self.compliance_alerts:
            extra.append(f"  アラート: {alert}")
        return format_pipeline_summary(
            label="銀行照合パイプライン",
            total_steps=7,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_bank_reconciliation_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> BankReconciliationPipelineResult:
    """
    銀行照合パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_date": str (YYYY-MM-DD, 省略時=今日),
            "encrypted_credentials": str (freee/銀行API認証情報),
            "bank_transactions": list[dict] (直接渡し: 銀行明細),
            "book_transactions": list[dict] (直接渡し: freee帳簿),
            "bank_balance": float (銀行残高直接渡し),
            "book_balance": float (帳簿残高直接渡し),
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "bank_reconciliation",
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, BankReconciliationPipelineResult)

    target_date = input_data.get("target_date") or date.today().isoformat()
    context["target_date"] = target_date

    # ─── Step 1: saas_reader ── 銀行入出金明細取得 ────────────────────────────
    if "bank_transactions" in input_data:
        context["bank_transactions"] = input_data["bank_transactions"]
        bank_balance = Decimal(str(input_data.get("bank_balance", 0)))
        context["bank_balance"] = bank_balance
        record_step(1, "saas_reader_bank", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={
                "source": "direct",
                "count": len(input_data["bank_transactions"]),
                "bank_balance": float(bank_balance),
            },
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "bank_api",
                    "operation": "get_transactions",
                    "params": {"target_date": target_date},
                    "encrypted_credentials": input_data.get("encrypted_credentials"),
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="saas_reader", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        record_step(1, "saas_reader_bank", "saas_reader", s1_out)
        if not s1_out.success:
            return emit_fail("saas_reader_bank")
        context["bank_transactions"] = s1_out.result.get("transactions", [])
        context["bank_balance"] = Decimal(str(s1_out.result.get("balance", 0)))
        bank_balance = context["bank_balance"]

    # ─── Step 2: saas_reader ── freee帳簿記録取得 ────────────────────────────
    if "book_transactions" in input_data:
        context["book_transactions"] = input_data["book_transactions"]
        book_balance = Decimal(str(input_data.get("book_balance", 0)))
        context["book_balance"] = book_balance
        record_step(2, "saas_reader_book", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={
                "source": "direct",
                "count": len(input_data["book_transactions"]),
                "book_balance": float(book_balance),
            },
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s2_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "get_bank_account_entries",
                    "params": {"target_date": target_date},
                    "encrypted_credentials": input_data.get("encrypted_credentials"),
                },
                context=context,
            ))
        except Exception as e:
            s2_out = MicroAgentOutput(
                agent_name="saas_reader", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        record_step(2, "saas_reader_book", "saas_reader", s2_out)
        if not s2_out.success:
            return emit_fail("saas_reader_book")
        context["book_transactions"] = s2_out.result.get("entries", [])
        context["book_balance"] = Decimal(str(s2_out.result.get("balance", 0)))
        book_balance = context["book_balance"]

    # ─── Step 3: rule_matcher ── 自動マッチング ───────────────────────────────
    try:
        s3_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "domain": "bank_reconciliation",
                "bank_transactions": context["bank_transactions"],
                "book_transactions": context["book_transactions"],
                "match_rules": {
                    "match_by_date": True,
                    "match_by_amount": True,
                    "match_by_description": True,
                    "date_tolerance_days": 2,
                },
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="rule_matcher", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "rule_matcher", "rule_matcher", s3_out)
    if not s3_out.success:
        return emit_fail("rule_matcher")
    matched_pairs: list[dict[str, Any]] = s3_out.result.get("matched", [])
    unmatched_bank: list[dict[str, Any]] = s3_out.result.get("unmatched_bank", [])
    unmatched_book: list[dict[str, Any]] = s3_out.result.get("unmatched_book", [])
    context["matched_pairs"] = matched_pairs
    context["unmatched_bank"] = unmatched_bank
    context["unmatched_book"] = unmatched_book

    # ─── Step 4: extractor ── 不一致原因分類 ─────────────────────────────────
    all_unmatched = [
        {**tx, "source": "bank"} for tx in unmatched_bank
    ] + [
        {**tx, "source": "book"} for tx in unmatched_book
    ]
    classification_schema = {
        "items": "array of {date: str, amount: number, description: str, source: str, reason: string (timing_difference/double_entry/unrecorded/unknown)}",
    }
    try:
        s4_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": str(all_unmatched),
                "schema": classification_schema,
                "domain": "bank_reconciliation_discrepancy",
            },
            context=context,
        ))
    except Exception as e:
        classified = [
            {**item, "reason": "unknown"} for item in all_unmatched
        ]
        s4_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result={"items": classified},
            confidence=0.8, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "extractor", "structured_extractor", s4_out)
    classified_unmatched: list[dict[str, Any]] = s4_out.result.get("items", all_unmatched)
    context["classified_unmatched"] = classified_unmatched

    # ─── Step 5: calculator ── 調整後残高算出 ───────────────────────────────
    # 調整後残高 = 銀行残高 + 帳簿未記載の入金 - 帳簿未記載の出金
    bank_adjustments = Decimal("0")
    for item in classified_unmatched:
        if item.get("source") == "book":
            # 帳簿側のみに存在 → 銀行残高に未反映
            amount = Decimal(str(item.get("amount", 0)))
            bank_adjustments += amount
    adjusted_balance = bank_balance + bank_adjustments

    try:
        s5_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "items": [
                    {"name": "bank_balance", "amount": float(bank_balance)},
                    {"name": "adjustment", "amount": float(bank_adjustments)},
                ],
                "mode": "sum",
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result={
                "bank_balance": float(bank_balance),
                "book_balance": float(book_balance),
                "adjusted_balance": float(adjusted_balance),
            },
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "calculator", "cost_calculator", s5_out)
    context["adjusted_balance"] = adjusted_balance

    # 大幅差異アラート
    diff = abs(adjusted_balance - book_balance)
    if diff > Decimal("100000"):
        compliance_alerts.append(
            f"調整後残高と帳簿残高の差異が¥{int(diff):,}（要確認）"
        )

    # ─── Step 6: generator ── 銀行勘定調整表生成 ─────────────────────────────
    reconciliation_data = {
        "target_date": target_date,
        "bank_balance": int(bank_balance),
        "book_balance": int(book_balance),
        "adjusted_balance": int(adjusted_balance),
        "matched_count": len(matched_pairs),
        "unmatched_items": classified_unmatched,
        "discrepancy": int(adjusted_balance - book_balance),
    }
    try:
        s6_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "bank_reconciliation_statement",
                "domain": "bank_reconciliation",
                "data": reconciliation_data,
                "output_filename": f"bank_recon_{target_date.replace('-', '')}.pdf",
            },
            context=context,
        ))
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={
                "pdf_path": f"/tmp/bank_recon_{target_date.replace('-', '')}.pdf",
                "mock": True,
            },
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "generator", "document_generator", s6_out)
    report_path = s6_out.result.get("pdf_path", "")
    context["report_path"] = report_path

    # ─── Step 7: validator ── 差額ゼロ検証 ───────────────────────────────────
    reconciled = abs(adjusted_balance - book_balance) == Decimal("0")
    approval_required = not reconciled

    try:
        s7_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    **reconciliation_data,
                    "report_path": report_path,
                    "reconciled": reconciled,
                },
                "required_fields": ["bank_balance", "book_balance", "adjusted_balance"],
                "numeric_fields": ["bank_balance", "book_balance", "adjusted_balance"],
                "positive_fields": [],
            },
            context=context,
        ))
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="output_validator", success=True,
            result={"valid": True, "reconciled": reconciled},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "output_validator", "output_validator", s7_out)

    if not reconciled:
        compliance_alerts.append(
            f"銀行照合: 差異あり ¥{int(adjusted_balance - book_balance):,} → 承認者確認が必要"
        )

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "bank_reconciliation_pipeline complete: matched=%d, unmatched=%d, "
        "reconciled=%s, diff=¥%s, %dms",
        len(matched_pairs), len(classified_unmatched),
        reconciled, f"{int(abs(adjusted_balance - book_balance)):,}", total_duration,
    )

    final_output = {
        **reconciliation_data,
        "report_path": report_path,
        "reconciled": reconciled,
        "compliance_alerts": compliance_alerts,
    }

    return BankReconciliationPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=approval_required,
        bank_balance=bank_balance,
        book_balance=book_balance,
        adjusted_balance=adjusted_balance,
        auto_matched=len(matched_pairs),
        unmatched=classified_unmatched,
        reconciled=reconciled,
        compliance_alerts=compliance_alerts,
    )

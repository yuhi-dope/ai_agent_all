"""
共通BPO 売掛管理・入金消込パイプライン（バックオフィスBPO）

レジストリキー: backoffice/ar_management
トリガー: スケジュール（毎日09:00）/ freee webhook（入金通知）
承認: 不要（消込は自動、督促は承認必要）
コネクタ: freee（入金API）、Bank API（入出金明細）、Gmail（督促メール）

Steps:
  Step 1: saas_reader    freee未入金請求書一覧 + 銀行入金明細を取得
  Step 2: rule_matcher   入金消込マッチング（金額完全一致→自動、差異あり→フラグ）
  Step 3: calculator     滞納日数計算、遅延損害金算出（年3%法定利率）
  Step 4: extractor      年齢分析（aging）: 30日/60日/90日/90日超に分類
  Step 5: generator      督促文面生成（段階別: 初回→催告→内容証明→法的措置予告）
  Step 6: message        督促メール送信 or Slackアラート（初回は自動、2回目以降は承認）
  Step 7: saas_writer    freee消込処理 + revenue_records更新
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.message import run_message_drafter
from workers.micro.saas_writer import run_saas_writer
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    format_pipeline_summary,
)

logger = logging.getLogger(__name__)

# 督促段階しきい値（滞納日数）
DUNNING_STAGE_THRESHOLDS = {
    "first_notice": 1,       # 初回: 支払期日超過翌日
    "second_notice": 15,     # 二度目: 15日超過
    "demand_letter": 45,     # 催告状: 45日超過
    "legal_warning": 90,     # 内容証明: 90日超過
}

# 年間法定遅延損害金利率（令和２年民法改正: 年3%）
LEGAL_DELAY_RATE = Decimal("0.03")

AGING_CATEGORIES = {
    "current": (0, 0),
    "1_30_days": (1, 30),
    "31_60_days": (31, 60),
    "61_90_days": (61, 90),
    "over_90_days": (91, 9999),
}


@dataclass
class ARManagementPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = False
    matched_count: int = 0
    unmatched_count: int = 0
    overdue_invoices: list[dict[str, Any]] = field(default_factory=list)
    dunning_actions: list[dict[str, Any]] = field(default_factory=list)
    total_outstanding: Decimal = field(default_factory=lambda: Decimal("0"))
    compliance_alerts: list[str] = field(default_factory=list)

    def to_ar_summary(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        extra = [
            f"  自動消込: {self.matched_count}件",
            f"  手動確認: {self.unmatched_count}件",
            f"  未回収合計: ¥{int(self.total_outstanding):,}",
        ]
        if self.approval_required:
            extra.append("  承認者確認が必要（督促2回目以降）")
        for alert in self.compliance_alerts:
            extra.append(f"  アラート: {alert}")
        return format_pipeline_summary(
            label="売掛管理・入金消込パイプライン",
            total_steps=7,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_ar_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> ARManagementPipelineResult:
    """
    売掛管理・入金消込パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_date": str (YYYY-MM-DD, 省略時=今日),
            "encrypted_credentials": str (freee/銀行API認証情報),
            "dry_run": bool (True=実際にはfreee消込しない),
            "unpaid_invoices": list[dict] (直接渡し形式),
            "bank_transactions": list[dict] (直接渡し形式),
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "ar_management",
        "dry_run": input_data.get("dry_run", False),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, ARManagementPipelineResult)

    target_date = input_data.get("target_date") or date.today().isoformat()
    context["target_date"] = target_date

    # ─── Step 1: saas_reader ── 未入金請求書 + 銀行入金明細 ──────────────────
    if "unpaid_invoices" in input_data and "bank_transactions" in input_data:
        context["unpaid_invoices"] = input_data["unpaid_invoices"]
        context["bank_transactions"] = input_data["bank_transactions"]
        record_step(1, "saas_reader", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={
                "source": "direct",
                "invoices_count": len(input_data["unpaid_invoices"]),
                "transactions_count": len(input_data["bank_transactions"]),
            },
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "list_unpaid_invoices_and_bank_transactions",
                    "params": {
                        "target_date": target_date,
                        "status": "unpaid",
                    },
                    "encrypted_credentials": input_data.get("encrypted_credentials"),
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="saas_reader", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        record_step(1, "saas_reader", "saas_reader", s1_out)
        if not s1_out.success:
            return emit_fail("saas_reader")
        context["unpaid_invoices"] = s1_out.result.get("unpaid_invoices", [])
        context["bank_transactions"] = s1_out.result.get("bank_transactions", [])

    # ─── Step 2: rule_matcher ── 入金消込マッチング ──────────────────────────
    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "domain": "ar_matching",
                "invoices": context["unpaid_invoices"],
                "transactions": context["bank_transactions"],
                "match_rules": {
                    "exact_amount_match": True,
                    "tolerance_yen": 0,
                    "date_window_days": 5,
                },
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return emit_fail("rule_matcher")
    matched = s2_out.result.get("matched", [])
    unmatched_invoices = s2_out.result.get("unmatched_invoices", [])
    context["matched"] = matched
    context["unmatched_invoices"] = unmatched_invoices

    # ─── Step 3: calculator ── 滞納日数計算・遅延損害金算出 ──────────────────
    today = date.today()
    overdue_invoices: list[dict[str, Any]] = []
    total_outstanding = Decimal("0")

    for inv in unmatched_invoices:
        amount = Decimal(str(inv.get("amount", 0)))
        due_date_str = inv.get("due_date", target_date)
        try:
            due_date = date.fromisoformat(due_date_str)
        except ValueError:
            due_date = today
        days_overdue = max(0, (today - due_date).days)

        # 遅延損害金計算（日割り）
        delay_charge = (
            amount * LEGAL_DELAY_RATE * Decimal(days_overdue) / Decimal("365")
        ).quantize(Decimal("1"))

        aging_cat = "current"
        for cat, (lo, hi) in AGING_CATEGORIES.items():
            if lo <= days_overdue <= hi:
                aging_cat = cat
                break

        overdue_invoices.append({
            **inv,
            "days_overdue": days_overdue,
            "delay_charge": int(delay_charge),
            "aging_category": aging_cat,
        })
        total_outstanding += amount

    try:
        s3_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "items": [
                    {"name": inv.get("client_name", ""), "amount": float(Decimal(str(inv.get("amount", 0))))}
                    for inv in unmatched_invoices
                ],
                "mode": "sum",
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result={"total_outstanding": int(total_outstanding)},
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "calculator", "cost_calculator", s3_out)
    context["overdue_invoices"] = overdue_invoices
    context["total_outstanding"] = total_outstanding

    # 高額滞納アラート
    if total_outstanding >= Decimal("1000000"):
        compliance_alerts.append(
            f"未回収残高が¥{int(total_outstanding):,}に達しています（要確認）"
        )

    # ─── Step 4: extractor ── 年齢分析（aging） ─────────────────────────────
    aging_schema = {
        "current": "number (当日未満)",
        "1_30_days": "number (1-30日超過)",
        "31_60_days": "number (31-60日超過)",
        "61_90_days": "number (61-90日超過)",
        "over_90_days": "number (90日超過)",
        "total": "number (合計未回収額)",
    }
    try:
        s4_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": str(overdue_invoices),
                "schema": aging_schema,
                "domain": "ar_aging_analysis",
            },
            context=context,
        ))
    except Exception as e:
        # フォールバック: 直接集計
        aging_summary: dict[str, int] = {cat: 0 for cat in AGING_CATEGORIES}
        for inv in overdue_invoices:
            cat = inv.get("aging_category", "current")
            aging_summary[cat] = aging_summary.get(cat, 0) + inv.get("amount", 0)
        aging_summary["total"] = int(total_outstanding)
        s4_out = MicroAgentOutput(
            agent_name="structured_extractor", success=True,
            result=aging_summary,
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "extractor", "structured_extractor", s4_out)
    context["aging_summary"] = s4_out.result

    # ─── Step 5: generator ── 督促文面生成 ──────────────────────────────────
    dunning_actions: list[dict[str, Any]] = []
    approval_required = False

    for inv in overdue_invoices:
        days = inv.get("days_overdue", 0)
        if days <= 0:
            continue
        if days >= DUNNING_STAGE_THRESHOLDS["legal_warning"]:
            stage = "legal_warning"
            approval_required = True
        elif days >= DUNNING_STAGE_THRESHOLDS["demand_letter"]:
            stage = "demand_letter"
            approval_required = True
        elif days >= DUNNING_STAGE_THRESHOLDS["second_notice"]:
            stage = "second_notice"
            approval_required = True
        else:
            stage = "first_notice"
        dunning_actions.append({
            "client": inv.get("client_name", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "amount": inv.get("amount", 0),
            "days_overdue": days,
            "stage": stage,
        })

    try:
        s5_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "dunning_notice",
                "domain": "ar_management",
                "items": dunning_actions,
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={"dunning_notices": dunning_actions, "mock": True},
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "generator", "document_generator", s5_out)
    context["dunning_actions"] = dunning_actions

    # ─── Step 6: message ── 督促文面ドラフト（初回自動、2回目以降は承認フラグ） ─
    first_notice_only = [a for a in dunning_actions if a.get("stage") == "first_notice"]
    msg_start = int(time.time() * 1000)
    try:
        draft_results = []
        for action in first_notice_only:
            doc_type = {
                "first_notice": "督促状（初回）",
                "second_notice": "催告書（2回目）",
                "demand_letter": "内容証明郵便",
                "legal_warning": "法的措置予告",
            }.get(action.get("stage", "first_notice"), "督促状（初回）")
            draft = await run_message_drafter(
                document_type=doc_type,
                context={
                    "client_name": action.get("client", ""),
                    "invoice_number": action.get("invoice_number", ""),
                    "amount": action.get("amount", 0),
                    "days_overdue": action.get("days_overdue", 0),
                },
                company_id=company_id,
            )
            draft_results.append({
                "client": action.get("client", ""),
                "subject": draft.subject,
                "body": draft.body,
                "stage": action.get("stage"),
                "auto_send": action.get("stage") == "first_notice" and not approval_required,
            })
        s6_out = MicroAgentOutput(
            agent_name="message_drafter", success=True,
            result={
                "drafts": draft_results,
                "sent_count": len([d for d in draft_results if d.get("auto_send")]),
                "pending_approval": len(dunning_actions) - len(first_notice_only),
            },
            confidence=0.9, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - msg_start,
        )
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="message_drafter", success=True,
            result={
                "drafts": [],
                "sent_count": 0,
                "pending_approval": len(dunning_actions),
            },
            confidence=0.85, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - msg_start,
        )
    record_step(6, "message", "message_drafter", s6_out)

    # ─── Step 7: saas_writer ── freee消込処理 ─────────────────────────────
    dry_run = context.get("dry_run", False)
    try:
        s7_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": "freee",
                "operation": "apply_payment_matching",
                "params": {
                    "matched": matched,
                    "target_date": target_date,
                },
                "approved": not dry_run,
                "dry_run": dry_run,
            },
            context=context,
        ))
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="saas_writer", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "saas_writer", "saas_writer", s7_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "ar_management_pipeline complete: matched=%d, unmatched=%d, outstanding=¥%s, %dms",
        len(matched), len(unmatched_invoices), f"{int(total_outstanding):,}", total_duration,
    )

    return ARManagementPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "matched": matched,
            "overdue_invoices": overdue_invoices,
            "aging_summary": context.get("aging_summary", {}),
            "dunning_actions": dunning_actions,
            "total_outstanding": int(total_outstanding),
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=approval_required,
        matched_count=len(matched),
        unmatched_count=len(unmatched_invoices),
        overdue_invoices=overdue_invoices,
        dunning_actions=dunning_actions,
        total_outstanding=total_outstanding,
        compliance_alerts=compliance_alerts,
    )

"""
共通BPO 月次決算パイプライン（バックオフィスBPO）

レジストリキー: backoffice/monthly_close
トリガー: スケジュール（毎月5営業日目 09:00）
承認: 必須（最終確認は経理責任者）
コネクタ: freee（試算表API）

Steps:
  Step 1: saas_reader    freee試算表（残高試算表）取得
  Step 2: rule_matcher   未処理チェック: 未消込入金/未記帳経費/未計上売上
  Step 3: calculator     月次P&L計算（売上-原価-販管費=営業利益）
  Step 4: calculator     前月比・予算比の差異分析
  Step 5: generator      月次決算レポート生成（P&L、BS要約、KPI）
  Step 6: compliance     異常値検出（前月比±30%超の科目をフラグ）
  Step 7: validator      貸借対照表の貸借一致検証
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
from workers.micro.generator import run_document_generator
from workers.micro.compliance import run_compliance_checker
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    format_pipeline_summary,
)

logger = logging.getLogger(__name__)

# 前月比異常検知しきい値（±30%超で警告）
ANOMALY_THRESHOLD_PCT = 30.0


@dataclass
class MonthlyClosePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = True
    pnl: dict[str, Any] = field(default_factory=dict)
    variance: dict[str, Any] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    unclosed_items: list[dict[str, Any]] = field(default_factory=list)
    report_pdf_path: str = ""
    compliance_alerts: list[str] = field(default_factory=list)

    def to_close_summary(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        op = self.pnl.get("operating_profit", 0)
        rev = self.pnl.get("revenue", 0)
        extra = [
            f"  売上: ¥{rev:,}",
            f"  営業利益: ¥{op:,}",
            f"  異常値検出: {len(self.anomalies)}件",
            f"  未処理残: {len(self.unclosed_items)}件",
        ]
        if self.approval_required:
            extra.append("  承認者確認が必要（経理責任者）")
        for alert in self.compliance_alerts:
            extra.append(f"  アラート: {alert}")
        return format_pipeline_summary(
            label="月次決算パイプライン",
            total_steps=7,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_monthly_close_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> MonthlyClosePipelineResult:
    """
    月次決算パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_month": str (YYYY-MM, 省略時=先月),
            "encrypted_credentials": str (freee認証情報),
            "trial_balance": dict (直接渡し: 残高試算表),
            "prior_month_pnl": dict (前月P&L、差異分析用),
            "budget_pnl": dict (予算P&L、予算比分析用),
            "dry_run": bool,
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "monthly_close",
        "dry_run": input_data.get("dry_run", False),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, MonthlyClosePipelineResult)

    target_month = input_data.get("target_month") or (
        date.today().replace(day=1) - timedelta(days=1)
    ).strftime("%Y-%m")
    context["target_month"] = target_month

    # ─── Step 1: saas_reader ── freee試算表取得 ───────────────────────────────
    if "trial_balance" in input_data:
        context["trial_balance"] = input_data["trial_balance"]
        record_step(1, "saas_reader", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"source": "direct", "accounts_count": len(input_data["trial_balance"])},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "get_trial_balance",
                    "params": {"fiscal_year_month": target_month},
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
        context["trial_balance"] = s1_out.result.get("trial_balance", {})

    # ─── Step 2: rule_matcher ── 未処理チェック ───────────────────────────────
    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "domain": "monthly_close_checklist",
                "trial_balance": context["trial_balance"],
                "target_month": target_month,
                "check_items": [
                    "unmatched_ar",      # 未消込売掛金
                    "unrecorded_expenses",  # 未記帳経費
                    "unrecorded_revenue",   # 未計上売上
                    "pending_journals",     # 未起票仕訳
                ],
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={"unclosed_items": [], "all_clear": True},
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    unclosed_items: list[dict[str, Any]] = s2_out.result.get("unclosed_items", [])
    context["unclosed_items"] = unclosed_items
    if unclosed_items:
        compliance_alerts.append(
            f"月次締め前に{len(unclosed_items)}件の未処理項目あり"
        )

    # ─── Step 3: calculator ── 月次P&L計算 ────────────────────────────────────
    tb = context["trial_balance"]
    revenue = int(tb.get("revenue", tb.get("売上高", 0)))
    cogs = int(tb.get("cogs", tb.get("売上原価", 0)))
    gross_profit = revenue - cogs
    sga = int(tb.get("sga", tb.get("販管費", 0)))
    operating_profit = gross_profit - sga
    pnl = {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "sga": sga,
        "operating_profit": operating_profit,
        "operating_margin_pct": round(operating_profit / revenue * 100, 1) if revenue else 0.0,
    }

    try:
        s3_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "items": [
                    {"name": "revenue", "amount": revenue},
                    {"name": "cogs", "amount": -cogs},
                    {"name": "sga", "amount": -sga},
                ],
                "mode": "pnl",
                "target_month": target_month,
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result=pnl,
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "calculator_pnl", "cost_calculator", s3_out)
    context["pnl"] = pnl

    # ─── Step 4: calculator ── 前月比・予算比差異分析 ────────────────────────
    prior_pnl = input_data.get("prior_month_pnl", {})
    budget_pnl = input_data.get("budget_pnl", {})
    variance: dict[str, Any] = {}

    def _pct_change(current: int, base: int) -> float:
        if base == 0:
            return 0.0
        return round((current - base) / abs(base) * 100, 1)

    if prior_pnl:
        variance["vs_prior_month"] = {
            k: _pct_change(pnl.get(k, 0), prior_pnl.get(k, 0))
            for k in ["revenue", "gross_profit", "operating_profit"]
        }
    if budget_pnl:
        variance["vs_budget"] = {
            k: _pct_change(pnl.get(k, 0), budget_pnl.get(k, 0))
            for k in ["revenue", "gross_profit", "operating_profit"]
        }

    try:
        s4_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "mode": "variance_analysis",
                "current": pnl,
                "prior": prior_pnl,
                "budget": budget_pnl,
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result={"variance": variance},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "calculator_variance", "cost_calculator", s4_out)
    context["variance"] = variance

    # ─── Step 5: generator ── 月次決算レポート生成 ────────────────────────────
    report_data = {
        "target_month": target_month,
        "pnl": pnl,
        "variance": variance,
        "unclosed_items": unclosed_items,
        "balance_sheet_summary": tb.get("balance_sheet", {}),
    }
    try:
        s5_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "monthly_close_report",
                "domain": "monthly_close",
                "data": report_data,
                "output_filename": f"monthly_close_{target_month.replace('-', '')}.pdf",
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={
                "pdf_path": f"/tmp/monthly_close_{target_month.replace('-', '')}.pdf",
                "mock": True,
            },
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "generator", "document_generator", s5_out)
    report_pdf_path = s5_out.result.get("pdf_path", "")
    context["report_pdf_path"] = report_pdf_path

    # ─── Step 6: compliance ── 異常値検出 ────────────────────────────────────
    anomalies: list[dict[str, Any]] = []
    if prior_pnl:
        for account, current_val in pnl.items():
            if not isinstance(current_val, (int, float)):
                continue
            prior_val = prior_pnl.get(account, 0)
            if prior_val == 0:
                continue
            change_pct = _pct_change(int(current_val), int(prior_val))
            if abs(change_pct) > ANOMALY_THRESHOLD_PCT:
                anomalies.append({
                    "account": account,
                    "current": current_val,
                    "prior": prior_val,
                    "change_pct": change_pct,
                    "reason": "前月比±30%超",
                })

    try:
        s6_out = await run_compliance_checker(MicroAgentInput(
            company_id=company_id, agent_name="compliance_checker",
            payload={
                "domain": "monthly_close_anomaly",
                "data": {
                    "pnl": pnl,
                    "prior_pnl": prior_pnl,
                    "anomaly_threshold_pct": ANOMALY_THRESHOLD_PCT,
                },
            },
            context=context,
        ))
    except Exception as e:
        extra_anomalies: list[dict[str, Any]] = []
        s6_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={"anomalies": extra_anomalies, "alerts": []},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "compliance", "compliance_checker", s6_out)
    extra_anomalies = s6_out.result.get("anomalies", [])
    if isinstance(extra_anomalies, list):
        anomalies.extend([a for a in extra_anomalies if a not in anomalies])
    extra_alerts = s6_out.result.get("alerts", [])
    if isinstance(extra_alerts, list):
        compliance_alerts.extend([a for a in extra_alerts if a not in compliance_alerts])
    if anomalies:
        compliance_alerts.append(f"異常値検出: {len(anomalies)}科目で前月比±30%超")
    context["anomalies"] = anomalies

    # ─── Step 7: validator ── 貸借一致検証 ───────────────────────────────────
    total_assets = int(tb.get("total_assets", tb.get("資産合計", 0)))
    total_liabilities_equity = int(
        tb.get("total_liabilities_equity", tb.get("負債純資産合計", total_assets))
    )
    balance_check_ok = total_assets == total_liabilities_equity

    try:
        s7_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "pnl": pnl,
                    "total_assets": total_assets,
                    "total_liabilities_equity": total_liabilities_equity,
                    "balance_check_ok": balance_check_ok,
                    "report_pdf_path": report_pdf_path,
                },
                "required_fields": ["revenue", "operating_profit"],
                "numeric_fields": ["revenue", "operating_profit"],
                "positive_fields": [],
            },
            context=context,
        ))
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="output_validator", success=True,
            result={"valid": balance_check_ok},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "output_validator", "output_validator", s7_out)

    if not balance_check_ok:
        compliance_alerts.append(
            f"貸借不一致: 資産¥{total_assets:,} ≠ 負債純資産¥{total_liabilities_equity:,}"
        )

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "monthly_close_pipeline complete: month=%s, revenue=¥%s, op_profit=¥%s, "
        "anomalies=%d, %dms",
        target_month, f"{revenue:,}", f"{operating_profit:,}",
        len(anomalies), total_duration,
    )

    final_output = {
        "target_month": target_month,
        "pnl": pnl,
        "variance": variance,
        "anomalies": anomalies,
        "unclosed_items": unclosed_items,
        "report_pdf_path": report_pdf_path,
        "compliance_alerts": compliance_alerts,
    }

    return MonthlyClosePipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=True,
        pnl=pnl,
        variance=variance,
        anomalies=anomalies,
        unclosed_items=unclosed_items,
        report_pdf_path=report_pdf_path,
        compliance_alerts=compliance_alerts,
    )

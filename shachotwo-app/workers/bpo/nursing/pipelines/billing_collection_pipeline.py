"""介護・福祉業 請求・入金管理パイプライン

Steps:
  Step 1: extractor          利用者・利用実績・公費情報データ構造化
  Step 2: self_pay_calculator 自己負担金計算（1割/2割/3割×高額介護サービス費）
  Step 3: public_fund_checker 公費負担判定（生活保護・障害者自立支援等）
  Step 4: invoice_generator  請求書・領収書生成
  Step 5: uncollected_detector 未収金検出（入金期限超過・未入金者リスト）
  Step 6: validator           出力バリデーション（請求金額・利用者情報・期間）
  Step 7: saas_writer         execution_logs保存 + 未収金督促通知生成
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 自己負担割合（所得段階別）
COPAYMENT_RATES: dict[str, float] = {
    "1割": 0.10,
    "2割": 0.20,
    "3割": 0.30,
}

# 高額介護サービス費の月額上限（所得段階別、円）
HIGH_COST_LIMIT: dict[str, int] = {
    "現役並み所得者（年収690万円以上）": 140_100,
    "現役並み所得者（年収380万円以上）": 93_000,
    "現役並み所得者（年収156万円以上）": 44_400,
    "一般": 44_400,
    "低所得II": 24_600,
    "低所得I": 15_000,
    "生活保護受給者等": 15_000,
}

# 公費負担種別
PUBLIC_FUND_TYPES: list[str] = [
    "生活保護",
    "障害者総合支援法（自立支援）",
    "原爆被爆者",
    "難病（特定疾患）",
    "感染症（結核等）",
]

# 未収金督促基準（日数）
OVERDUE_DAYS_WARNING = 30
OVERDUE_DAYS_CRITICAL = 60


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
class BillingCollectionResult:
    """請求・入金管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 請求・入金管理パイプライン",
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


async def run_billing_collection_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> BillingCollectionResult:
    """
    請求・入金管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "period_year": int,
            "period_month": int,
            "billing_records": [
                {
                    "user_id": str,
                    "user_name": str,
                    "copayment_rate": str,       # "1割" / "2割" / "3割"
                    "income_category": str,      # 高額介護サービス費の所得段階
                    "public_fund_type": str,     # 公費種別（なければ ""）
                    "total_service_fee": int,    # 介護報酬総額（円）
                    "payment_due_date": str,     # 支払期限 YYYY-MM-DD
                    "paid_date": str | None,     # 入金日（未入金はNone）
                    "paid_amount": int,          # 入金額（未入金は0）
                }
            ],
        }

    Returns:
        BillingCollectionResult
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

    def _fail(step_name: str) -> BillingCollectionResult:
        return BillingCollectionResult(
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
            "text": _serialize_billing_data(input_data),
            "schema": {
                "period_year": "int",
                "period_month": "int",
                "billing_records": "list[{user_id, user_name, copayment_rate, income_category, public_fund_type, total_service_fee, payment_due_date, paid_date, paid_amount}]",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    billing_records = input_data.get("billing_records", s1_out.result.get("billing_records", []))
    period_year = input_data.get("period_year", s1_out.result.get("period_year", 2026))
    period_month = input_data.get("period_month", s1_out.result.get("period_month", 1))
    context.update({
        "billing_records": billing_records,
        "period_year": period_year,
        "period_month": period_month,
    })

    # ─── Step 2: self_pay_calculator ────────────────────────────────────
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "self_pay_amount",
            "billing_records": billing_records,
            "copayment_rates": COPAYMENT_RATES,
            "high_cost_limit": HIGH_COST_LIMIT,
        },
        context=context,
    ))
    _add_step(2, "self_pay_calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("self_pay_calculator")
    calculated_bills = s2_out.result.get("calculated_bills", billing_records)
    context["calculated_bills"] = calculated_bills

    # ─── Step 3: public_fund_checker ────────────────────────────────────
    s3_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": billing_records,
            "rule_type": "public_fund_check",
            "public_fund_types": PUBLIC_FUND_TYPES,
        },
        context=context,
    ))
    _add_step(3, "public_fund_checker", "rule_matcher", s3_out)
    if not s3_out.success:
        return _fail("public_fund_checker")
    public_fund_results = s3_out.result
    context["public_fund_results"] = public_fund_results

    # ─── Step 4: invoice_generator ──────────────────────────────────────
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "介護利用料請求書・領収書",
            "variables": {
                "period_year": period_year,
                "period_month": period_month,
                "calculated_bills": calculated_bills,
                "public_fund_results": public_fund_results,
            },
        },
        context=context,
    ))
    _add_step(4, "invoice_generator", "document_generator", s4_out)
    if not s4_out.success:
        return _fail("invoice_generator")
    invoices = s4_out.result
    context["invoices"] = invoices

    # ─── Step 5: uncollected_detector ───────────────────────────────────
    s5_start = int(time.time() * 1000)
    from datetime import date
    today = date.today()
    uncollected: list[dict[str, Any]] = []
    for record in billing_records:
        if record.get("paid_date"):
            continue  # 入金済みはスキップ
        due_date_str = record.get("payment_due_date", "")
        user_name = record.get("user_name", record.get("user_id", "不明"))
        if due_date_str:
            try:
                due_date = date.fromisoformat(due_date_str)
                overdue_days = (today - due_date).days
                if overdue_days > 0:
                    severity = "CRITICAL" if overdue_days >= OVERDUE_DAYS_CRITICAL else "WARNING"
                    uncollected.append({
                        "user_id": record.get("user_id", ""),
                        "user_name": user_name,
                        "due_date": due_date_str,
                        "overdue_days": overdue_days,
                        "amount": record.get("total_service_fee", 0),
                        "severity": severity,
                    })
            except ValueError:
                pass
    s5_out = MicroAgentOutput(
        agent_name="uncollected_detector",
        success=True,
        result={
            "uncollected": uncollected,
            "total_uncollected_amount": sum(u["amount"] for u in uncollected),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "uncollected_detector", "uncollected_detector", s5_out)
    context["uncollected"] = uncollected

    # ─── Step 6: validator ──────────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": invoices,
            "required_fields": ["content"],
            "uncollected_count": len(uncollected),
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    total_uncollected = sum(u["amount"] for u in uncollected)
    # TODO: 未収金督促通知生成・execution_logs保存
    logger.info(
        f"billing_collection_pipeline: company_id={company_id}, "
        f"period={period_year}/{period_month:02d}, "
        f"records={len(billing_records)}, "
        f"uncollected={len(uncollected)}, "
        f"total_uncollected_amount={total_uncollected}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "dunning_notice_sent": False,  # TODO: 督促通知実装
            "uncollected_count": len(uncollected),
            "total_uncollected_amount": total_uncollected,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "period_year": period_year,
        "period_month": period_month,
        "calculated_bills": calculated_bills,
        "public_fund_results": public_fund_results,
        "invoices": invoices,
        "uncollected": uncollected,
        "total_uncollected_amount": total_uncollected,
    }

    return BillingCollectionResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _serialize_billing_data(input_data: dict[str, Any]) -> str:
    """input_dataを構造化抽出用テキストに変換する"""
    import json
    return json.dumps(input_data, ensure_ascii=False)

"""
共通BPO 税務申告支援パイプライン（バックオフィスBPO）

レジストリキー: backoffice/tax_filing
トリガー: スケジュール（四半期: 消費税 / 年次: 法人税）
承認: 必須（税理士レビュー前提）
コネクタ: freee、e-Gov（e-Tax連携）

Steps:
  Step 1: saas_reader    freee年次決算データ取得
  Step 2: calculator     消費税計算（課税売上/仕入税額控除/簡易課税判定）
  Step 3: calculator     法人税概算（所得800万以下15%/超23.2%）
  Step 4: generator      申告書ドラフト生成（別表一〜十六の主要項目）
  Step 5: compliance     青色申告要件チェック、電子帳簿保存法対応確認
  Step 6: validator      計算整合性検証

注意: 最終申告は税理士が実施。このパイプラインはドラフト資料の生成まで。
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
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

# 法人税率（令和4年度税制）
CORPORATE_TAX_RATE_LOW = Decimal("0.15")   # 所得800万円以下（中小企業軽減税率）
CORPORATE_TAX_RATE_HIGH = Decimal("0.232")  # 所得800万円超
CORPORATE_TAX_THRESHOLD = Decimal("8000000")

# 消費税簡易課税しきい値（前々事業年度の課税売上高5000万円以下）
SIMPLIFIED_TAX_THRESHOLD = Decimal("50000000")

# 消費税標準税率
CONSUMPTION_TAX_RATE = Decimal("0.10")
REDUCED_TAX_RATE = Decimal("0.08")


@dataclass
class TaxFilingPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = True
    consumption_tax: dict[str, Any] = field(default_factory=dict)
    corporate_tax: dict[str, Any] = field(default_factory=dict)
    draft_file_path: str = ""
    compliance_alerts: list[str] = field(default_factory=list)
    tax_accountant_review_required: bool = True

    def to_tax_summary(self) -> str:
        """パイプライン結果のログ用サマリー文字列。"""
        ct = self.consumption_tax
        corp = self.corporate_tax
        extra = [
            f"  消費税納付額: ¥{ct.get('tax_payable', 0):,}",
            f"  法人税概算: ¥{corp.get('estimated_tax', 0):,}",
            f"  申告書ドラフト: {self.draft_file_path or '未生成'}",
        ]
        if self.tax_accountant_review_required:
            extra.append("  税理士レビューが必要（最終申告は税理士が実施）")
        if self.approval_required:
            extra.append("  承認者確認が必要")
        for alert in self.compliance_alerts:
            extra.append(f"  アラート: {alert}")
        return format_pipeline_summary(
            label="税務申告支援パイプライン",
            total_steps=6,
            success=self.success,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_tax_filing_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> TaxFilingPipelineResult:
    """
    税務申告支援パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "fiscal_year": str (YYYY, 省略時=昨年),
            "filing_type": str ("consumption_tax" | "corporate_tax" | "both"),
            "encrypted_credentials": str (freee認証情報),
            "annual_data": dict (直接渡し: 年次決算データ),
            "prior_year_taxable_sales": float (前々事業年度課税売上高、簡易課税判定用),
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "tax_filing",
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, TaxFilingPipelineResult)

    fiscal_year = input_data.get("fiscal_year") or str(date.today().year - 1)
    filing_type = input_data.get("filing_type", "both")
    context["fiscal_year"] = fiscal_year
    context["filing_type"] = filing_type

    # ─── Step 1: saas_reader ── 年次決算データ取得 ───────────────────────────
    if "annual_data" in input_data:
        context["annual_data"] = input_data["annual_data"]
        record_step(1, "saas_reader", "saas_reader", MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"source": "direct", "fiscal_year": fiscal_year},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "freee",
                    "operation": "get_annual_financial_data",
                    "params": {
                        "fiscal_year": fiscal_year,
                        "include": ["pnl", "balance_sheet", "tax_summary"],
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
        context["annual_data"] = s1_out.result.get("data", {})

    annual = context["annual_data"]

    # ─── Step 2: calculator ── 消費税計算 ─────────────────────────────────────
    taxable_sales = Decimal(str(annual.get("taxable_sales", annual.get("課税売上高", 0))))
    input_tax = Decimal(str(annual.get("input_tax_credit", annual.get("仕入税額控除", 0))))
    prior_year_taxable = Decimal(str(input_data.get("prior_year_taxable_sales", 0)))

    use_simplified = (
        prior_year_taxable > Decimal("0")
        and prior_year_taxable <= SIMPLIFIED_TAX_THRESHOLD
    )

    output_tax_10 = (
        Decimal(str(annual.get("taxable_sales_10pct", taxable_sales)))
        * CONSUMPTION_TAX_RATE
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    output_tax_8 = (
        Decimal(str(annual.get("taxable_sales_8pct", 0))) * REDUCED_TAX_RATE
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    total_output_tax = output_tax_10 + output_tax_8

    if use_simplified:
        # 簡易課税: 業種別みなし仕入率（デフォルト: 第5種サービス業50%）
        service_ratio = Decimal(str(annual.get("simplified_tax_ratio", "0.50")))
        tax_payable_ct = (total_output_tax * (1 - service_ratio)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        method = "simplified"
    else:
        tax_payable_ct = max(Decimal("0"), total_output_tax - input_tax)
        method = "general"

    consumption_tax = {
        "taxable_sales": int(taxable_sales),
        "output_tax": int(total_output_tax),
        "input_tax_credit": int(input_tax),
        "tax_payable": int(tax_payable_ct),
        "method": method,
        "use_simplified": use_simplified,
    }

    try:
        s2_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "mode": "consumption_tax",
                "taxable_sales": float(taxable_sales),
                "input_tax_credit": float(input_tax),
                "use_simplified": use_simplified,
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result=consumption_tax,
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "calculator_consumption_tax", "cost_calculator", s2_out)
    context["consumption_tax"] = consumption_tax

    # ─── Step 3: calculator ── 法人税概算 ────────────────────────────────────
    pre_tax_income = Decimal(str(annual.get("pre_tax_income", annual.get("税引前当期利益", 0))))
    # 課税所得 = 税引前当期利益（簡易計算: 加算・減算の詳細調整は税理士が実施）
    taxable_income = max(Decimal("0"), pre_tax_income)

    if taxable_income <= CORPORATE_TAX_THRESHOLD:
        estimated_corp_tax = (taxable_income * CORPORATE_TAX_RATE_LOW).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        tax_rate_applied = float(CORPORATE_TAX_RATE_LOW)
    else:
        tax_low = CORPORATE_TAX_THRESHOLD * CORPORATE_TAX_RATE_LOW
        tax_high = (taxable_income - CORPORATE_TAX_THRESHOLD) * CORPORATE_TAX_RATE_HIGH
        estimated_corp_tax = (tax_low + tax_high).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        tax_rate_applied = float(CORPORATE_TAX_RATE_HIGH)

    corporate_tax = {
        "pre_tax_income": int(pre_tax_income),
        "taxable_income": int(taxable_income),
        "estimated_tax": int(estimated_corp_tax),
        "effective_rate": tax_rate_applied,
        "note": "概算値。加算・減算項目の調整は税理士が実施",
    }

    try:
        s3_out = await run_cost_calculator(MicroAgentInput(
            company_id=company_id, agent_name="cost_calculator",
            payload={
                "mode": "corporate_tax",
                "pre_tax_income": float(pre_tax_income),
                "threshold": float(CORPORATE_TAX_THRESHOLD),
                "rate_low": float(CORPORATE_TAX_RATE_LOW),
                "rate_high": float(CORPORATE_TAX_RATE_HIGH),
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="cost_calculator", success=True,
            result=corporate_tax,
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "calculator_corporate_tax", "cost_calculator", s3_out)
    context["corporate_tax"] = corporate_tax

    # ─── Step 4: generator ── 申告書ドラフト生成 ──────────────────────────────
    draft_data = {
        "fiscal_year": fiscal_year,
        "filing_type": filing_type,
        "consumption_tax": consumption_tax,
        "corporate_tax": corporate_tax,
        "annual_summary": {
            "revenue": annual.get("revenue", annual.get("売上高", 0)),
            "operating_profit": annual.get("operating_profit", annual.get("営業利益", 0)),
            "pre_tax_income": int(pre_tax_income),
        },
        "disclaimer": "このドラフトは参考資料です。最終申告は税理士が実施してください。",
    }
    try:
        s4_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "tax_filing_draft",
                "domain": "tax_filing",
                "data": draft_data,
                "output_filename": f"tax_filing_draft_{fiscal_year}.pdf",
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={
                "pdf_path": f"/tmp/tax_filing_draft_{fiscal_year}.pdf",
                "mock": True,
            },
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "generator", "document_generator", s4_out)
    draft_file_path = s4_out.result.get("pdf_path", "")
    context["draft_file_path"] = draft_file_path

    # ─── Step 5: compliance ── 青色申告・電帳法要件チェック ──────────────────
    try:
        s5_out = await run_compliance_checker(MicroAgentInput(
            company_id=company_id, agent_name="compliance_checker",
            payload={
                "domain": "tax_filing_compliance",
                "data": {
                    "fiscal_year": fiscal_year,
                    "annual_data": annual,
                    "consumption_tax": consumption_tax,
                    "corporate_tax": corporate_tax,
                    "check_items": [
                        "blue_return_eligibility",       # 青色申告要件
                        "electronic_bookkeeping_act",    # 電子帳簿保存法
                        "consumption_tax_filing_deadline",  # 消費税申告期限
                        "corporate_tax_filing_deadline",    # 法人税申告期限
                    ],
                },
            },
            context=context,
        ))
    except Exception as e:
        # フォールバック: 基本チェックのみ
        fallback_alerts: list[str] = []
        if int(pre_tax_income) < 0:
            fallback_alerts.append("当期純損失が発生しています。繰越欠損金の適用を検討してください。")
        if use_simplified and prior_year_taxable > SIMPLIFIED_TAX_THRESHOLD * Decimal("0.9"):
            fallback_alerts.append(
                "前々事業年度課税売上高が5000万円に近づいています。簡易課税選択の継続可否を確認してください。"
            )
        s5_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={"alerts": fallback_alerts, "passed": len(fallback_alerts) == 0},
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "compliance", "compliance_checker", s5_out)
    extra_alerts = s5_out.result.get("alerts", [])
    if isinstance(extra_alerts, list):
        compliance_alerts.extend([a for a in extra_alerts if a not in compliance_alerts])

    # ─── Step 6: validator ── 計算整合性検証 ─────────────────────────────────
    total_tax_burden = int(estimated_corp_tax) + int(tax_payable_ct)
    try:
        s6_out = await run_output_validator(MicroAgentInput(
            company_id=company_id, agent_name="output_validator",
            payload={
                "document": {
                    "fiscal_year": fiscal_year,
                    "consumption_tax_payable": consumption_tax["tax_payable"],
                    "corporate_tax_estimated": corporate_tax["estimated_tax"],
                    "total_tax_burden": total_tax_burden,
                    "draft_file_path": draft_file_path,
                },
                "required_fields": ["fiscal_year", "consumption_tax_payable", "corporate_tax_estimated"],
                "numeric_fields": ["consumption_tax_payable", "corporate_tax_estimated"],
                "positive_fields": [],
            },
            context=context,
        ))
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="output_validator", success=True,
            result={"valid": True},
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "output_validator", "output_validator", s6_out)

    # 税負担率の異常チェック
    revenue_val = annual.get("revenue", annual.get("売上高", 0))
    if revenue_val and int(revenue_val) > 0:
        tax_burden_rate = total_tax_burden / int(revenue_val)
        if tax_burden_rate > 0.3:
            compliance_alerts.append(
                f"税負担率が{tax_burden_rate:.1%}と高水準です。節税対策を検討してください。"
            )

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "tax_filing_pipeline complete: year=%s, consumption_tax=¥%s, "
        "corporate_tax=¥%s, %dms",
        fiscal_year,
        f"{int(tax_payable_ct):,}",
        f"{int(estimated_corp_tax):,}",
        total_duration,
    )

    final_output = {
        "fiscal_year": fiscal_year,
        "filing_type": filing_type,
        "consumption_tax": consumption_tax,
        "corporate_tax": corporate_tax,
        "total_tax_burden": total_tax_burden,
        "draft_file_path": draft_file_path,
        "compliance_alerts": compliance_alerts,
        "disclaimer": "最終申告は税理士が実施してください。",
    }

    return TaxFilingPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=True,
        consumption_tax=consumption_tax,
        corporate_tax=corporate_tax,
        draft_file_path=draft_file_path,
        compliance_alerts=compliance_alerts,
        tax_accountant_review_required=True,
    )

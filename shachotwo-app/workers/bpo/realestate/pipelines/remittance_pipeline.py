"""不動産業 送金・入金管理パイプライン

Steps:
  Step 1: transaction_reader    取引データ取得（入金記録・契約データ・敷金台帳）
  Step 2: invoice_generator     適格請求書生成（インボイス制度対応。T+13桁・税率区分）
  Step 3: fee_validator         仲介手数料上限チェック（宅建業法46条。超過時はerrorで停止）
  Step 4: deposit_manager       敷金台帳更新（退去時精算=敷金-原状回復費。経過年数考慮）
  Step 5: remittance_calculator オーナー送金額計算（賃料-管理手数料-修繕費+更新料分配）
  Step 6: output_generator      帳票出力（請求書PDF+送金明細PDF）
  Step 7: output_validator      バリデーション（金額整合性・必須記載事項チェック）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.generator import run_document_generator
from workers.micro.compliance import run_compliance_checker
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 仲介手数料上限（宅建業法46条・報酬告示）
AGENCY_FEE_RATE_SALE_LOW = Decimal("0.055")   # 200万円以下
AGENCY_FEE_RATE_SALE_MID = Decimal("0.044")   # 200-400万円
AGENCY_FEE_RATE_SALE_HIGH = Decimal("0.033")  # 400万円超
AGENCY_FEE_FIXED_HIGH = Decimal("66000")      # 税込固定額（400万超の速算式）
AGENCY_FEE_RATE_LEASE = Decimal("1.1")        # 賃貸: 賃料の1.1ヶ月分（税込）

# 敷金原状回復の耐用年数
RESTORATION_USEFUL_LIFE: dict[str, int] = {
    "壁紙": 6, "クロス": 6, "カーペット": 6,
    "エアコン": 6, "給湯器": 10, "換気扇": 10, "default": 6,
}

# インボイス必須記載事項（消費税法57条の4）
INVOICE_REQUIRED_FIELDS = [
    "issuer_name", "registration_number",  # T+13桁
    "transaction_date", "transaction_content",
    "tax_rate", "tax_excluded_amount", "tax_amount",
    "recipient_name",
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
class RemittanceResult:
    """送金・入金管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 送金・入金管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        remittance = self.final_output.get("remittance_amount", 0)
        lines.append(f"  送金額: ¥{remittance:,}円")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _calc_agency_fee_limit(transaction_price: int, fee_type: str) -> int:
    """仲介手数料の上限額を計算する（税込）。"""
    if fee_type == "sale":
        price = Decimal(str(transaction_price))
        if price <= 2_000_000:
            return int(price * AGENCY_FEE_RATE_SALE_LOW)
        elif price <= 4_000_000:
            return int(price * AGENCY_FEE_RATE_SALE_MID)
        else:
            return int(price * AGENCY_FEE_RATE_SALE_HIGH + AGENCY_FEE_FIXED_HIGH)
    elif fee_type == "lease":
        # 賃料の1.1ヶ月分（上限）
        return int(Decimal(str(transaction_price)) * AGENCY_FEE_RATE_LEASE)
    return 0


def _calc_deposit_refund(
    deposit_amount: int,
    tenure_months: int,
    restoration_items: list[dict],
) -> dict[str, Any]:
    """
    退去時の敷金精算額を計算する。
    経過年数考慮（国交省ガイドライン準拠）。
    """
    total_tenant_share = 0
    breakdown: list[dict] = []
    for item in restoration_items:
        cost = int(item.get("cost", 0))
        category = item.get("category", "default")
        useful_life = RESTORATION_USEFUL_LIFE.get(category, RESTORATION_USEFUL_LIFE["default"])
        tenure_years = tenure_months / 12
        # 残存価値 = max(1 - tenure_years / useful_life, 0.10)
        residual_ratio = max(1.0 - tenure_years / useful_life, 0.10)
        tenant_share = int(cost * residual_ratio)
        total_tenant_share += tenant_share
        breakdown.append({
            "category": category,
            "cost": cost,
            "tenure_years": round(tenure_years, 1),
            "useful_life": useful_life,
            "residual_ratio": round(residual_ratio, 3),
            "tenant_share": tenant_share,
        })

    refund_amount = max(deposit_amount - total_tenant_share, 0)
    return {
        "deposit_amount": deposit_amount,
        "total_tenant_share": total_tenant_share,
        "refund_amount": refund_amount,
        "breakdown": breakdown,
    }


async def run_remittance_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> RemittanceResult:
    """
    送金・入金管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "period_year": int,            # 対象年
            "period_month": int,           # 対象月
            "property_id": str,            # 物件ID
            "transaction_type": str,       # sale / lease（仲介手数料チェック用）
            "transaction_price": int,      # 取引価格 or 月額賃料（仲介手数料上限計算用）
            "agency_fee": int,             # 実際の仲介手数料（上限チェック対象）
            "management_fee_rate": float,  # 管理委託料率（例: 0.05 = 5%）
            "rent_collected": int,         # 当月回収賃料合計
            "repair_cost": int,            # 修繕費（オーナー負担分）
            "renewal_fee_share": int,      # 更新料オーナー分配
            "arrears_collected": int,      # 滞納回収分
            # 敷金精算の場合
            "deposit_amount": int,         # 敷金預り額
            "tenure_months": int,          # 入居期間（ヶ月）
            "restoration_items": list,     # [{category, cost}]
            # インボイス用
            "invoice_registration_number": str,  # T+13桁登録番号
        }

    Returns:
        RemittanceResult
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

    def _fail(step_name: str) -> RemittanceResult:
        return RemittanceResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: transaction_reader ──────────────────────────────────────
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_sources": ["payment_records", "contract_data", "deposit_ledger"],
            "property_id": input_data.get("property_id", ""),
            "period_year": input_data.get("period_year"),
            "period_month": input_data.get("period_month"),
        },
        context=context,
    ))
    _add_step(1, "transaction_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("transaction_reader")
    transaction_data = {**input_data, **s1_out.result}
    context["transaction_data"] = transaction_data

    # ─── Step 2: invoice_generator ───────────────────────────────────────
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "適格請求書",
            "variables": {
                "issuer_registration_number": input_data.get("invoice_registration_number", ""),
                "transaction_date": f"{input_data.get('period_year')}-{input_data.get('period_month'):02d}-01",
                "items": [
                    {
                        "description": "管理委託料",
                        "amount": int(
                            input_data.get("rent_collected", 0) *
                            input_data.get("management_fee_rate", 0.05)
                        ),
                        "tax_rate": 0.10,
                        "is_taxable": True,   # 管理委託料は課税取引
                    },
                    {
                        "description": "賃料（居住用）",
                        "amount": input_data.get("rent_collected", 0),
                        "tax_rate": 0.00,
                        "is_taxable": False,  # 居住用賃貸は非課税
                    },
                ],
                "required_fields": INVOICE_REQUIRED_FIELDS,
            },
            "purpose": "インボイス制度対応適格請求書生成",
        },
        context=context,
    ))
    _add_step(2, "invoice_generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("invoice_generator")
    invoice_data = s2_out.result
    context["invoice_data"] = invoice_data

    # ─── Step 3: fee_validator ────────────────────────────────────────────
    agency_fee = input_data.get("agency_fee", 0)
    transaction_price = input_data.get("transaction_price", 0)
    transaction_type = input_data.get("transaction_type", "lease")
    fee_limit = _calc_agency_fee_limit(transaction_price, transaction_type)

    s3_start = int(time.time() * 1000)
    fee_exceeded = (agency_fee > 0) and (fee_limit > 0) and (agency_fee > fee_limit)
    if fee_exceeded:
        logger.error(
            f"[remittance] 仲介手数料上限超過: 請求額=¥{agency_fee:,}, "
            f"上限=¥{fee_limit:,} (宅建業法46条違反)"
        )
    s3_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=not fee_exceeded,
        result={
            "agency_fee": agency_fee,
            "fee_limit": fee_limit,
            "exceeded": fee_exceeded,
            "violation": "宅建業法46条・報酬告示: 仲介手数料が上限を超えています" if fee_exceeded else None,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "fee_validator", "compliance_checker", s3_out)
    if fee_exceeded:
        return _fail("fee_validator")
    context["fee_validation"] = s3_out.result

    # ─── Step 4: deposit_manager ─────────────────────────────────────────
    deposit_amount = input_data.get("deposit_amount", 0)
    if deposit_amount > 0:
        deposit_calc = _calc_deposit_refund(
            deposit_amount=deposit_amount,
            tenure_months=input_data.get("tenure_months", 0),
            restoration_items=input_data.get("restoration_items", []),
        )
        s4_start = int(time.time() * 1000)
        s4_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result=deposit_calc,
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    else:
        s4_start = int(time.time() * 1000)
        s4_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={"skipped": True, "reason": "敷金精算対象外"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    _add_step(4, "deposit_manager", "rule_matcher", s4_out)
    context["deposit_result"] = s4_out.result

    # ─── Step 5: remittance_calculator ───────────────────────────────────
    s5_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "owner_remittance",
            "rent_collected": int(input_data.get("rent_collected", 0)),
            "management_fee_rate": float(input_data.get("management_fee_rate", 0.05)),
            "repair_cost": int(input_data.get("repair_cost", 0)),
            "ad_cost": int(input_data.get("ad_cost", 0)),
            "renewal_fee_share": int(input_data.get("renewal_fee_share", 0)),
            "arrears_collected": int(input_data.get("arrears_collected", 0)),
            "deposit_refund": s4_out.result.get("refund_amount", 0) if deposit_amount > 0 else 0,
        },
        context=context,
    ))
    _add_step(5, "remittance_calculator", "cost_calculator", s5_out)
    if not s5_out.success:
        return _fail("remittance_calculator")
    remittance_amount = s5_out.result.get("remittance_amount", 0)
    context["remittance_amount"] = remittance_amount

    # ─── Step 6: output_generator ─────────────────────────────────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "送金明細書",
            "variables": {
                "period_year": input_data.get("period_year"),
                "period_month": input_data.get("period_month"),
                "property_id": input_data.get("property_id", ""),
                "rent_collected": input_data.get("rent_collected", 0),
                "management_fee": int(
                    input_data.get("rent_collected", 0) *
                    input_data.get("management_fee_rate", 0.05)
                ),
                "repair_cost": input_data.get("repair_cost", 0),
                "renewal_fee_share": input_data.get("renewal_fee_share", 0),
                "arrears_collected": input_data.get("arrears_collected", 0),
                "remittance_amount": remittance_amount,
                "deposit_result": s4_out.result,
                "invoice_data": invoice_data,
            },
            "output_format": "pdf",
        },
        context=context,
    ))
    _add_step(6, "output_generator", "document_generator", s6_out)
    context["output_documents"] = s6_out.result

    # ─── Step 7: output_validator ─────────────────────────────────────────
    s7_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "remittance_amount": remittance_amount,
                "invoice_data": invoice_data,
                "deposit_result": s4_out.result,
            },
            "required_fields": ["remittance_amount", "invoice_data"],
            "check_type": "remittance_completeness",
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", s7_out)

    final_output = {
        "period": f"{input_data.get('period_year')}-{input_data.get('period_month'):02d}",
        "property_id": input_data.get("property_id", ""),
        "invoice_data": invoice_data,
        "fee_validation": s3_out.result,
        "deposit_result": s4_out.result,
        "remittance_amount": remittance_amount,
        "remittance_breakdown": s5_out.result,
        "output_documents": s6_out.result,
        "validation": s7_out.result,
    }

    return RemittanceResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

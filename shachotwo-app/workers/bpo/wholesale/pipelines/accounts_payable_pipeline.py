"""卸売業 仕入・買掛管理パイプライン

Steps:
  Step 1: purchase_data_reader    仕入データ読み込み（発注/仕入先マスタ/実績）
  Step 2: order_generator         発注書自動生成（仕入先別集約+最低発注金額チェック）
  Step 3: receiving_inspector     検品・検収処理（品目/数量/単価の三者照合。差異±3%）
  Step 4: invoice_matcher         請求書照合（OCR読取→自社仕入データ突合。差異検出）
  Step 5: rebate_calculator       リベート計算（数量/達成/早期支払/新商品）
  Step 6: payment_scheduler       買掛金管理・支払予定（残高更新+資金繰り予測）
  Step 7: output_validator        バリデーション

リベート計算式:
  数量リベート: 年間仕入額に応じた段階的割戻し
  達成リベート: 目標達成率100%以上で仕入額の0.5%
  早期支払リベート: 10日以上早期支払で請求額の1.0%
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.generator import run_document_generator
from workers.micro.diff import run_diff_detector
from workers.micro.ocr import run_document_ocr
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 検収許容誤差（数量）
RECEIVING_TOLERANCE_RATIO = 0.03  # ±3%

# リベート条件テーブル（年間仕入額 → 割戻し率）
VOLUME_REBATE_TABLE = [
    (50_000_000, 0.020),   # 5,000万円超 → 2.0%
    (30_000_000, 0.015),   # 3,000万円超 → 1.5%
    (10_000_000, 0.010),   # 1,000万円超 → 1.0%
    (0, 0.000),
]

# 達成リベート率
ACHIEVEMENT_REBATE_RATE = 0.005  # 0.5%

# 早期支払リベート（10日以上早期）
EARLY_PAYMENT_DAYS = 10
EARLY_PAYMENT_REBATE_RATE = 0.010  # 1.0%

CONFIDENCE_WARNING_THRESHOLD = 0.70


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
class AccountsPayableResult:
    """仕入・買掛管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 仕入・買掛管理パイプライン",
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


async def run_accounts_payable_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> AccountsPayableResult:
    """
    卸売業 仕入・買掛管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "reorder_proposals": list[dict],    # 在庫パイプラインの発注提案
            "supplier_master": list[dict],      # 仕入先マスタ（最低発注額/締日/支払条件）
            "purchase_history": list[dict],     # 仕入実績（リベート計算用）
            "delivery_data": list[dict],        # 入荷・納品データ
            "supplier_invoices": list[dict],    # 仕入先請求書（URL or テキスト）
            "current_payables": list[dict],     # 現在の買掛金残高
            "period_start": str,                # 集計期間開始 (YYYY-MM-DD)
            "period_end": str,                  # 集計期間終了 (YYYY-MM-DD)
        }

    Returns:
        AccountsPayableResult
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

    def _fail(step_name: str) -> AccountsPayableResult:
        return AccountsPayableResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: purchase_data_reader ────────────────────────────────────
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "purchase_status",
            "reorder_proposals": input_data.get("reorder_proposals", []),
            "supplier_master": input_data.get("supplier_master", []),
            "purchase_history": input_data.get("purchase_history", []),
            "period_start": input_data.get("period_start", ""),
            "period_end": input_data.get("period_end", ""),
        },
        context=context,
    ))
    _add_step(1, "purchase_data_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("purchase_data_reader")
    purchase_status = s1_out.result
    context["purchase_status"] = purchase_status

    # ─── Step 2: order_generator ─────────────────────────────────────────
    # 発注書自動生成（仕入先別集約 + 最低発注金額チェック）
    s2_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "発注書",
            "variables": {
                "reorder_proposals": input_data.get("reorder_proposals", []),
                "supplier_master": input_data.get("supplier_master", []),
                # 同一仕入先の複数商品を1発注にまとめる
                "aggregate_by_supplier": True,
                # 仕入先ごとの最低発注金額チェック
                "check_min_order_amount": True,
                "output_formats": ["pdf", "csv"],  # PDF + FAX/メール用CSV
            },
        },
        context=context,
    ))
    _add_step(2, "order_generator", "document_generator", s2_out)
    if not s2_out.success:
        return _fail("order_generator")
    purchase_orders = s2_out.result
    context["purchase_orders"] = purchase_orders

    # ─── Step 3: receiving_inspector ─────────────────────────────────────
    # 検品・検収（品目/数量/単価の三者照合。差異±3%以内はOK）
    s3_out = await run_diff_detector(MicroAgentInput(
        company_id=company_id,
        agent_name="diff_detector",
        payload={
            "diff_type": "receiving_inspection",
            "delivery_data": input_data.get("delivery_data", []),
            "purchase_orders": purchase_orders,
            "tolerance_ratio": RECEIVING_TOLERANCE_RATIO,
            # 差異あり → 差異レポート + 仕入先連絡文書
            # 検収OK → 在庫反映（入庫処理）
        },
        context=context,
    ))
    _add_step(3, "receiving_inspector", "diff_detector", s3_out)
    if not s3_out.success:
        return _fail("receiving_inspector")
    receiving_result = s3_out.result
    context["receiving_result"] = receiving_result

    # ─── Step 4: invoice_matcher ──────────────────────────────────────────
    # 仕入先請求書のOCR読取 + 自社仕入データとの突合
    supplier_invoices = input_data.get("supplier_invoices", [])
    invoice_texts: list[str] = []
    for inv in supplier_invoices:
        if inv.get("document_url"):
            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id,
                agent_name="document_ocr",
                payload={"file_path": inv["document_url"], "language": "ja"},
                context=context,
            ))
            invoice_texts.append(ocr_out.result.get("text", ""))
        elif inv.get("raw_text"):
            invoice_texts.append(inv["raw_text"])

    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "invoice_matching",
            "supplier_invoice_texts": invoice_texts,
            "receiving_result": receiving_result,
            # 差異パターン: 単価違い/数量違い/未納品請求/二重請求
        },
        context=context,
    ))
    _add_step(4, "invoice_matcher", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("invoice_matcher")
    invoice_match = s4_out.result
    context["invoice_match"] = invoice_match

    # ─── Step 5: rebate_calculator ───────────────────────────────────────
    # リベート計算（数量/達成/早期支払/新商品）
    s5_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "rebate_calculation",
            "purchase_history": input_data.get("purchase_history", []),
            "supplier_master": input_data.get("supplier_master", []),
            "volume_rebate_table": VOLUME_REBATE_TABLE,
            "achievement_rebate_rate": ACHIEVEMENT_REBATE_RATE,
            "early_payment_days": EARLY_PAYMENT_DAYS,
            "early_payment_rebate_rate": EARLY_PAYMENT_REBATE_RATE,
            "period_end": input_data.get("period_end", ""),
        },
        context=context,
    ))
    _add_step(5, "rebate_calculator", "cost_calculator", s5_out)
    rebate_result = s5_out.result
    context["rebate_result"] = rebate_result

    # ─── Step 6: payment_scheduler ───────────────────────────────────────
    # 買掛金管理・支払予定（資金繰り予測 + 早期支払判断）
    # 予測残高 = 現在残高 + 入金予定 - 支払予定
    s6_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "payment_schedule",
            "invoice_match": invoice_match,
            "rebate_result": rebate_result,
            "current_payables": input_data.get("current_payables", []),
            "supplier_master": input_data.get("supplier_master", []),
            # 早期支払割引額 > 資金コスト(借入金利) → 早期支払推奨
            "borrowing_rate": 0.02,  # 借入金利デフォルト2%
        },
        context=context,
    ))
    _add_step(6, "payment_scheduler", "cost_calculator", s6_out)
    if not s6_out.success:
        return _fail("payment_scheduler")
    payment_schedule = s6_out.result
    context["payment_schedule"] = payment_schedule

    # ─── Step 7: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "purchase_orders": purchase_orders,
                "receiving_result": receiving_result,
                "invoice_match": invoice_match,
                "rebate_result": rebate_result,
                "payment_schedule": payment_schedule,
            },
            "required_fields": ["purchase_orders", "payment_schedule"],
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", val_out)

    discrepancies = invoice_match.get("discrepancies", [])
    early_payment_recommendations = payment_schedule.get("early_payment_recommendations", [])

    final_output = {
        "purchase_orders": purchase_orders,
        "receiving_result": receiving_result,
        "invoice_match": invoice_match,
        "rebate_result": rebate_result,
        "payment_schedule": payment_schedule,
        "discrepancy_count": len(discrepancies),
        "rebate_total_yen": rebate_result.get("total_rebate_yen", 0),
        "early_payment_recommendations": early_payment_recommendations,
    }

    return AccountsPayableResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

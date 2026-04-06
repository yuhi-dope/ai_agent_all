"""卸売業 請求・売掛管理パイプライン

Steps:
  Step 1: sales_data_reader   売上データ読み込み（締日別・得意先別集計）
  Step 2: price_calculator    売価計算（得意先別掛率+数量割引+キャンペーン値引き）
  Step 3: invoice_generator   請求書自動生成（インボイス制度対応+電子帳簿保存法対応）
  Step 4: receivable_manager  売掛金管理（残高更新+入金消込ファジーマッチ+支払期日超過アラート）
  Step 5: credit_manager      与信管理（与信限度額チェック+支払遅延パターン検出）
  Step 6: output_validator    バリデーション

法令根拠:
  インボイス制度: 消費税法第57条の4第1項（適格請求書の必須記載事項）
  電子帳簿保存法: 電子帳簿保存法第7条
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.compliance import run_compliance_checker
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 支払遅延アラート閾値（日）
OVERDUE_NOTIFY_DAYS = 7
OVERDUE_DEMAND_DAYS = 30
OVERDUE_SUSPEND_DAYS = 60
OVERDUE_LEGAL_DAYS = 90

# 与信限度額警告閾値
CREDIT_WARNING_RATIO = 0.80
CREDIT_SUSPEND_RATIO = 1.00

# 数量割引テーブル（個数 → 割引率）
QUANTITY_DISCOUNT_TABLE = [
    (1000, 0.08),
    (500, 0.05),
    (100, 0.03),
    (1, 0.00),
]

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
class AccountsReceivableResult:
    """請求・売掛管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 請求・売掛管理パイプライン",
            f"  ステップ: {len(self.steps)}/6",
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


async def run_accounts_receivable_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> AccountsReceivableResult:
    """
    卸売業 請求・売掛管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "closing_date": str,           # 締日 (YYYY-MM-DD)
            "sales_details": list[dict],   # 売上明細（当期分）
            "customer_master": list[dict], # 得意先マスタ（掛率/締日/与信情報）
            "bank_data": list[dict],       # 入金データ（振込明細）
            "previous_receivables": list[dict], # 前期未収残高
            "invoice_reg_number": str,     # 自社インボイス登録番号 (T+13桁)
        }

    Returns:
        AccountsReceivableResult
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

    def _fail(step_name: str) -> AccountsReceivableResult:
        return AccountsReceivableResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: sales_data_reader ───────────────────────────────────────
    # 売上データを締日別・得意先別に集計
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "sales_summary",
            "closing_date": input_data.get("closing_date", ""),
            "sales_details": input_data.get("sales_details", []),
            "customer_master": input_data.get("customer_master", []),
        },
        context=context,
    ))
    _add_step(1, "sales_data_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("sales_data_reader")
    sales_summary = s1_out.result
    context["sales_summary"] = sales_summary

    # ─── Step 2: price_calculator ────────────────────────────────────────
    # 得意先別掛率・数量割引・キャンペーン適用
    # 請求単価 = 定価 × 掛率 × (1 - 数量割引率) × (1 - キャンペーン割引率)
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "wholesale_pricing",
            "sales_summary": sales_summary,
            "customer_master": input_data.get("customer_master", []),
            "quantity_discount_table": QUANTITY_DISCOUNT_TABLE,
            # 端数処理: 円未満切捨て
            "rounding": "floor",
        },
        context=context,
    ))
    _add_step(2, "price_calculator", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("price_calculator")
    priced_details = s2_out.result
    context["priced_details"] = priced_details

    # ─── Step 3: invoice_generator ───────────────────────────────────────
    # インボイス制度対応請求書の自動生成
    # 前回請求残高 + 当期売上 - 入金 - 値引返品 = 今回請求額
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "適格請求書",
            "variables": {
                "priced_details": priced_details,
                "previous_receivables": input_data.get("previous_receivables", []),
                "closing_date": input_data.get("closing_date", ""),
                "invoice_reg_number": input_data.get("invoice_reg_number", ""),
                # インボイス必須記載事項（消費税法第57条の4第1項）:
                # 登録番号/取引年月日/取引内容/税率別対価/税率別消費税額/交付先名称
                "tax_breakdown": True,   # 税率ごと（10%/8%）の内訳表示
                "electronic_storage": True,  # 電子帳簿保存法対応（タイムスタンプ）
            },
        },
        context=context,
    ))
    _add_step(3, "invoice_generator", "document_generator", s3_out)
    if not s3_out.success:
        return _fail("invoice_generator")
    invoices = s3_out.result
    context["invoices"] = invoices

    # ─── Step 4: receivable_manager ──────────────────────────────────────
    # 売掛金残高更新 + 入金消込（ファジーマッチ）+ 支払期日超過アラート
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "receivable_management",
            "invoices": invoices,
            "bank_data": input_data.get("bank_data", []),
            "previous_receivables": input_data.get("previous_receivables", []),
            "matching_config": {
                # 振込名義と得意先名のLevenshtein距離でファジーマッチ
                "fuzzy_match_threshold": 0.80,
                # 金額差異 ≤ 振込手数料(660円) → 手数料差引で自動消込
                "transfer_fee_tolerance": 660,
            },
            "overdue_thresholds": {
                "notify": OVERDUE_NOTIFY_DAYS,
                "demand": OVERDUE_DEMAND_DAYS,
                "suspend": OVERDUE_SUSPEND_DAYS,
                "legal": OVERDUE_LEGAL_DAYS,
            },
        },
        context=context,
    ))
    _add_step(4, "receivable_manager", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("receivable_manager")
    receivable_status = s4_out.result
    context["receivable_status"] = receivable_status

    # ─── Step 5: credit_manager ──────────────────────────────────────────
    # 与信管理（与信限度額チェック + 支払遅延パターン検出）
    s5_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id,
        agent_name="compliance_checker",
        payload={
            "check_type": "credit_management",
            "receivable_status": receivable_status,
            "customer_master": input_data.get("customer_master", []),
            "credit_config": {
                "warning_ratio": CREDIT_WARNING_RATIO,   # 80%超→警告
                "suspend_ratio": CREDIT_SUSPEND_RATIO,   # 100%超→出荷停止提案
                # 支払遅延スコア = Σ(遅延日数 × 遅延金額) / 取引総額
                "delay_score_period_months": 12,
            },
        },
        context=context,
    ))
    _add_step(5, "credit_manager", "compliance_checker", s5_out)
    credit_report = s5_out.result
    context["credit_report"] = credit_report

    # ─── Step 6: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "invoices": invoices,
                "receivable_status": receivable_status,
                "credit_report": credit_report,
            },
            "required_fields": ["invoices", "receivable_status"],
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    # アラート集計
    overdue_alerts = receivable_status.get("overdue_alerts", [])
    credit_alerts = credit_report.get("credit_alerts", [])
    manual_match_queue = receivable_status.get("manual_match_queue", [])

    final_output = {
        "closing_date": input_data.get("closing_date", ""),
        "invoices": invoices,
        "receivable_status": receivable_status,
        "credit_report": credit_report,
        "overdue_alerts": overdue_alerts,
        "credit_alerts": credit_alerts,
        "manual_match_queue": manual_match_queue,
        "invoice_count": len(invoices.get("invoices", [])),
        "overdue_count": len(overdue_alerts),
    }

    return AccountsReceivableResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

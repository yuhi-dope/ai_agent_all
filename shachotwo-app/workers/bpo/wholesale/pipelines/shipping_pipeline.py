"""卸売業 物流・配送管理パイプライン

Steps:
  Step 1: order_data_reader       受注データ読み込み（出荷対象リスト生成）
  Step 2: shipping_optimizer      配送方法最適化（自社便vs宅配便判定+最安キャリア選定）
  Step 3: picking_list_generator  ピッキングリスト生成（ロケーション順ソート+FIFO指示）
  Step 4: document_generator      出荷書類自動生成（出荷指示書+納品書+送り状CSV）
  Step 5: tracking_manager        配送状況追跡（各社追跡API→配達完了/遅延/不在検知）
  Step 6: output_validator        バリデーション

配送コスト判定:
  自社便: 固定費 / 件数 + 変動費(燃料)
  宅配便: サイズ別法人契約単価（60-160サイズ）
  大口(パレット) → 自社便/路線便。小口(120サイズ以下) → 宅配便
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
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 宅配便サイズ別運賃（関東圏内、法人契約単価の目安）
DELIVERY_SIZE_RATES = {
    60: 650,
    80: 850,
    100: 1050,
    120: 1300,
    140: 1600,
    160: 1900,
}

# 自社便コスト（円/件）: 月60万円(固定)/22日/15件 + 燃料500円
DEFAULT_OWN_DELIVERY_COST_PER_STOP = 2300

# 自社便が有利になる荷物サイズ閾値（これより大きければ自社便）
OWN_DELIVERY_SIZE_THRESHOLD = 120

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
class ShippingResult:
    """物流・配送管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 物流・配送管理パイプライン",
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


async def run_shipping_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> ShippingResult:
    """
    卸売業 物流・配送管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "order_data": list[dict],          # 出荷対象の受注データ（在庫引当済み）
            "inventory_allocation": list[dict], # 在庫引当結果（ロット/ロケーション情報含む）
            "delivery_addresses": list[dict],   # 配送先住所
            "carrier_config": dict | None,      # 利用キャリア設定（ヤマト/佐川/日本郵便）
            "own_vehicle_available": bool,      # 自社便が使える場合True
            "tracking_numbers": list[str] | None, # 追跡番号（追跡専用実行時）
            "ship_date": str,                   # 出荷予定日 (YYYY-MM-DD)
        }

    Returns:
        ShippingResult
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

    def _fail(step_name: str) -> ShippingResult:
        return ShippingResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: order_data_reader ───────────────────────────────────────
    # 出荷対象の受注データ読み込み
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "shipping_targets",
            "order_data": input_data.get("order_data", []),
            "inventory_allocation": input_data.get("inventory_allocation", []),
            "ship_date": input_data.get("ship_date", ""),
        },
        context=context,
    ))
    _add_step(1, "order_data_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("order_data_reader")
    shipping_targets = s1_out.result.get("shipping_targets", input_data.get("order_data", []))
    context["shipping_targets"] = shipping_targets

    # ─── Step 2: shipping_optimizer ──────────────────────────────────────
    # 配送方法最適化（自社便 vs 宅配便 vs 路線便）
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "shipping_optimization",
            "shipping_targets": shipping_targets,
            "delivery_addresses": input_data.get("delivery_addresses", []),
            "own_vehicle_available": input_data.get("own_vehicle_available", False),
            "own_delivery_cost_per_stop": DEFAULT_OWN_DELIVERY_COST_PER_STOP,
            "delivery_size_rates": DELIVERY_SIZE_RATES,
            "own_delivery_size_threshold": OWN_DELIVERY_SIZE_THRESHOLD,
            # 近距離+大口 → 自社便、近距離+小口 → 宅配便、遠距離 → 宅配便/路線便
        },
        context=context,
    ))
    _add_step(2, "shipping_optimizer", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("shipping_optimizer")
    optimized_shipping = s2_out.result
    context["optimized_shipping"] = optimized_shipping

    # ─── Step 3: picking_list_generator ──────────────────────────────────
    # ピッキングリスト生成（ロケーション順ソート+FIFO指示+欠品アラート）
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "ピッキングリスト",
            "variables": {
                "optimized_shipping": optimized_shipping,
                "inventory_allocation": input_data.get("inventory_allocation", []),
                # ロケーション順ソート（倉庫内の動線最適化）
                "sort_by_location": True,
                # FIFO指示（賞味期限の古いロットから出庫）
                "fifo_instruction": True,
            },
        },
        context=context,
    ))
    _add_step(3, "picking_list_generator", "document_generator", s3_out)
    picking_list = s3_out.result
    context["picking_list"] = picking_list

    # ─── Step 4: document_generator ──────────────────────────────────────
    # 出荷書類一式の自動生成
    carrier_config = input_data.get("carrier_config") or {}
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "出荷書類セット",
            "variables": {
                "optimized_shipping": optimized_shipping,
                "ship_date": input_data.get("ship_date", ""),
                # 出荷指示書 + 納品書（得意先別フォーマット）
                # 送り状CSV:
                #   ヤマト → B2クラウドCSVフォーマット
                #   佐川  → e飛伝CSVフォーマット
                #   日本郵便 → ゆうプリRCSVフォーマット
                "carrier_config": carrier_config,
                # 物流ラベル（バーコード/QR付き）
                "include_barcode_label": True,
            },
        },
        context=context,
    ))
    _add_step(4, "document_generator", "document_generator", s4_out)
    shipping_documents = s4_out.result
    context["shipping_documents"] = shipping_documents

    # ─── Step 5: tracking_manager ────────────────────────────────────────
    # 配送状況追跡（送り状番号から各社APIで追跡）
    tracking_numbers = input_data.get("tracking_numbers") or \
        shipping_documents.get("tracking_numbers", [])

    s5_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_type": "tracking_status",
            "tracking_numbers": tracking_numbers,
            "carrier_config": carrier_config,
            # 配達完了 → 納品ステータス更新
            # 配達遅延 → アラート + 得意先への通知提案
            # 不在持戻り → 再配達手配提案
        },
        context=context,
    ))
    _add_step(5, "tracking_manager", "saas_reader", s5_out)
    tracking_status = s5_out.result
    context["tracking_status"] = tracking_status

    # ─── Step 6: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "picking_list": picking_list,
                "shipping_documents": shipping_documents,
                "tracking_status": tracking_status,
            },
            "required_fields": ["picking_list", "shipping_documents"],
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    delay_alerts = tracking_status.get("delay_alerts", [])
    stockout_items = picking_list.get("stockout_items", [])

    final_output = {
        "shipping_targets": shipping_targets,
        "optimized_shipping": optimized_shipping,
        "picking_list": picking_list,
        "shipping_documents": shipping_documents,
        "tracking_status": tracking_status,
        "delay_alerts": delay_alerts,
        "stockout_items": stockout_items,
        "total_shipments": len(shipping_targets),
        "delay_count": len(delay_alerts),
    }

    return ShippingResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

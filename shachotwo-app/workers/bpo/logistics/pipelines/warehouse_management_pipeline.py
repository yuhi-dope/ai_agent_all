"""物流・運送業 倉庫管理パイプライン

Steps:
  Step 1: extractor           入出庫データ構造化（荷主×品目×数量×ロケーション）
  Step 2: inventory_calc      在庫残高計算（入庫－出庫＝残高・ロケーション別）
  Step 3: location_optimizer  ロケーション最適化（ABC分析→出荷頻度高は手前配置）
  Step 4: stocktake_checker   棚卸差異チェック（帳簿在庫 vs 実地棚卸）
  Step 5: storage_fee_calc    保管料計算（保管面積×保管日数×単価）
  Step 6: generator           入出庫報告書・棚卸表・保管料請求書生成
  Step 7: saas_writer         execution_logs保存 + 在庫アラート通知
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

# 棚卸差異許容範囲（%）
STOCKTAKE_TOLERANCE_RATE = 0.01  # 1%以内

# 保管料計算単位（円/坪/月）
DEFAULT_STORAGE_RATE_YEN_PER_TSUBO = 3_000

# ABC分析閾値
ABC_A_THRESHOLD = 0.70  # 上位70%の出荷量 → Aクラス
ABC_B_THRESHOLD = 0.90  # 上位90%の出荷量 → Bクラス（残りはC）


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
class WarehouseManagementResult:
    """倉庫管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 倉庫管理パイプライン",
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


async def run_warehouse_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> WarehouseManagementResult:
    """
    倉庫管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "target_month": str,            # YYYY-MM
            "warehouse_id": str,
            "movements": list[{
                "movement_id": str,
                "movement_date": str,       # YYYY-MM-DD
                "movement_type": str,       # inbound/outbound
                "item_code": str,
                "item_name": str,
                "quantity": int,
                "location_code": str,       # 棚番号
                "shipper_id": str,
            }],
            "current_inventory": list[{
                "item_code": str,
                "item_name": str,
                "book_quantity": int,       # 帳簿在庫数
                "actual_quantity": int,     # 実地棚卸数（棚卸時のみ）
                "location_code": str,
                "area_tsubo": float,        # 占有面積（坪）
                "monthly_shipment_count": int,  # 月間出荷頻度
            }],
            "storage_rate_yen": float,      # 保管単価（円/坪/月）
            "billing_days": int,            # 請求対象日数
        }

    Returns:
        WarehouseManagementResult
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

    def _fail(step_name: str) -> WarehouseManagementResult:
        return WarehouseManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    movements = input_data.get("movements", [])
    inventory = input_data.get("current_inventory", [])

    # ─── Step 1: extractor ──────────────────────────────────────────────
    import json
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": json.dumps(input_data, ensure_ascii=False),
            "schema": {
                "target_month": "string",
                "warehouse_id": "string",
                "movements": "list",
                "current_inventory": "list",
            },
        },
        context=context,
    ))
    _add_step(1, "extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")
    context.update({k: input_data.get(k) for k in input_data})

    # ─── Step 2: inventory_calc（在庫残高計算）──────────────────────────
    s2_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "inventory_balance",
            "movements": movements,
            "current_inventory": inventory,
        },
        context=context,
    ))
    _add_step(2, "inventory_calc", "cost_calculator", s2_out)
    if not s2_out.success:
        return _fail("inventory_calc")
    context["inventory_balance"] = s2_out.result

    # ─── Step 3: location_optimizer（ロケーション最適化・ABC分析）────────
    s3_start = int(time.time() * 1000)
    # ABC分析: 月間出荷頻度で分類
    sorted_items = sorted(
        inventory, key=lambda x: x.get("monthly_shipment_count", 0), reverse=True
    )
    total_shipments = sum(i.get("monthly_shipment_count", 0) for i in sorted_items)

    abc_result: list[dict[str, Any]] = []
    cumulative = 0
    for item in sorted_items:
        shipment_count = item.get("monthly_shipment_count", 0)
        cumulative += shipment_count
        ratio = cumulative / total_shipments if total_shipments > 0 else 0.0
        abc_class = "A" if ratio <= ABC_A_THRESHOLD else ("B" if ratio <= ABC_B_THRESHOLD else "C")
        abc_result.append({
            "item_code": item.get("item_code", ""),
            "item_name": item.get("item_name", ""),
            "monthly_shipment_count": shipment_count,
            "abc_class": abc_class,
            "current_location": item.get("location_code", ""),
        })

    s3_out = MicroAgentOutput(
        agent_name="location_optimizer",
        success=True,
        result={
            "abc_analysis": abc_result,
            "a_class_count": sum(1 for i in abc_result if i["abc_class"] == "A"),
            "b_class_count": sum(1 for i in abc_result if i["abc_class"] == "B"),
            "c_class_count": sum(1 for i in abc_result if i["abc_class"] == "C"),
        },
        confidence=0.90,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "location_optimizer", "location_optimizer", s3_out)
    context["abc_result"] = s3_out.result

    # ─── Step 4: stocktake_checker（棚卸差異チェック）──────────────────
    s4_start = int(time.time() * 1000)
    stocktake_discrepancies: list[dict[str, Any]] = []
    for item in inventory:
        book_qty = item.get("book_quantity", 0)
        actual_qty = item.get("actual_quantity")
        if actual_qty is None:
            continue  # 棚卸未実施はスキップ
        diff = actual_qty - book_qty
        diff_rate = abs(diff) / book_qty if book_qty > 0 else (1.0 if diff != 0 else 0.0)
        if diff_rate > STOCKTAKE_TOLERANCE_RATE:
            stocktake_discrepancies.append({
                "item_code": item.get("item_code", ""),
                "item_name": item.get("item_name", ""),
                "book_quantity": book_qty,
                "actual_quantity": actual_qty,
                "difference": diff,
                "diff_rate_pct": round(diff_rate * 100, 2),
            })

    s4_out = MicroAgentOutput(
        agent_name="stocktake_checker",
        success=True,
        result={
            "discrepancies": stocktake_discrepancies,
            "discrepancy_count": len(stocktake_discrepancies),
            "passed": len(stocktake_discrepancies) == 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "stocktake_checker", "stocktake_checker", s4_out)
    context["stocktake_result"] = s4_out.result

    # ─── Step 5: storage_fee_calc（保管料計算）──────────────────────────
    storage_rate = input_data.get(
        "storage_rate_yen", DEFAULT_STORAGE_RATE_YEN_PER_TSUBO
    )
    billing_days = input_data.get("billing_days", 30)

    storage_fee_details: list[dict[str, Any]] = []
    total_storage_fee = 0.0
    for item in inventory:
        area = item.get("area_tsubo", 0.0)
        daily_rate = storage_rate / 30
        fee = area * daily_rate * billing_days
        total_storage_fee += fee
        storage_fee_details.append({
            "item_code": item.get("item_code", ""),
            "area_tsubo": area,
            "billing_days": billing_days,
            "fee_yen": round(fee, 0),
        })

    s5_out = MicroAgentOutput(
        agent_name="storage_fee_calc",
        success=True,
        result={
            "storage_fee_details": storage_fee_details,
            "total_storage_fee_yen": round(total_storage_fee, 0),
            "storage_rate_yen": storage_rate,
            "billing_days": billing_days,
        },
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - pipeline_start,
    )
    _add_step(5, "storage_fee_calc", "storage_fee_calc", s5_out)
    context["storage_fee"] = s5_out.result

    # ─── Step 6: generator（各種帳票生成）──────────────────────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "倉庫管理報告書",
            "variables": {
                "target_month": input_data.get("target_month", ""),
                "warehouse_id": input_data.get("warehouse_id", ""),
                "inventory_balance": s2_out.result,
                "abc_analysis": s3_out.result,
                "stocktake_discrepancies": stocktake_discrepancies,
                "storage_fee_total": round(total_storage_fee, 0),
            },
        },
        context=context,
    ))
    _add_step(6, "generator", "document_generator", s6_out)
    context["generated_doc"] = s6_out.result

    # ─── Step 7: saas_writer ────────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    logger.info(
        f"warehouse_management_pipeline: company_id={company_id}, "
        f"warehouse={input_data.get('warehouse_id', '')}, "
        f"discrepancies={len(stocktake_discrepancies)}"
    )
    s7_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={
            "logged": True,
            "slack_notified": False,  # TODO: 棚卸差異アラート時はSlack通知
            "stocktake_discrepancy_count": len(stocktake_discrepancies),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "saas_writer", "saas_writer", s7_out)

    final_output = {
        "inventory_balance": s2_out.result,
        "abc_analysis": s3_out.result,
        "stocktake_discrepancies": stocktake_discrepancies,
        "storage_fee": s5_out.result,
        "generated_doc": s6_out.result,
    }

    return WarehouseManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )

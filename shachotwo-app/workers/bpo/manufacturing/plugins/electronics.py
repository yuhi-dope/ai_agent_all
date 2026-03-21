"""電子部品プラグイン — BOM展開の計算ロジック"""
from __future__ import annotations

from typing import Any

from workers.bpo.manufacturing.models import (
    AdditionalCostItem,
    CustomerOverrides,
    HearingInput,
    ProcessEstimate,
)
from workers.bpo.manufacturing.plugins.base import ManufacturingPlugin


class ElectronicsPlugin(ManufacturingPlugin):
    """
    電子部品（中分類28-29）用プラグイン。

    金属加工と異なる点:
    - BOM（部品表）からの部品費積上げ
    - SMT実装とスルーホール実装の使い分け
    - 基板製作は外注が一般的
    """

    @property
    def sub_industry_id(self) -> str:
        return "electronics"

    @property
    def display_name(self) -> str:
        return "電子部品・電気機械"

    @property
    def jsic_codes(self) -> list[str]:
        return ["E-28", "E-29"]

    async def estimate_processes(
        self,
        hearing: HearingInput,
        yaml_config: dict[str, Any] | None,
        customer: CustomerOverrides,
    ) -> list[ProcessEstimate]:
        """電子部品の工程推定"""
        processes: list[ProcessEstimate] = []
        order = 1

        # 基板製作（外注）
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="基板製作（外注）",
            equipment="外注",
            equipment_type="pcb_fabrication",
            setup_time_min=0,
            cycle_time_min=0,
            is_outsource=True,
            confidence=0.5,
            notes="プリント基板製造",
        ))
        order += 1

        # SMT実装（表面実装）
        bom_count = len(hearing.bom) if hearing.bom else 20  # デフォルト20点
        # SMT実装は1点あたり約0.5-2秒
        smt_cycle_min = round(bom_count * 1.0 / 60, 2)  # 1点1秒想定
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="SMT実装",
            equipment="マウンター",
            equipment_type="smt_mounter",
            setup_time_min=60,
            cycle_time_min=smt_cycle_min,
            confidence=0.5,
            notes=f"部品 {bom_count}点想定",
        ))
        order += 1

        # リフロー
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="リフローはんだ付け",
            equipment="リフロー炉",
            equipment_type="reflow",
            setup_time_min=30,
            cycle_time_min=0.5,
            confidence=0.7,
        ))
        order += 1

        # 手はんだ（スルーホール部品がある場合）
        has_through_hole = any(
            item.get("mount_type") == "through_hole"
            for item in (hearing.bom or [])
        )
        if has_through_hole or not hearing.bom:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="手はんだ",
                equipment="はんだごて",
                equipment_type="manual_soldering",
                setup_time_min=10,
                cycle_time_min=5,
                confidence=0.5,
                notes="スルーホール部品",
            ))
            order += 1

        # 組立
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="組立",
            equipment="手作業",
            equipment_type="assembly",
            setup_time_min=15,
            cycle_time_min=10,
            confidence=0.5,
        ))
        order += 1

        # 検査
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="検査・通電テスト",
            equipment="検査治具",
            equipment_type="inspection",
            setup_time_min=15,
            cycle_time_min=3,
            confidence=0.6,
        ))

        return processes

    def calculate_additional_costs(
        self,
        hearing: HearingInput,
        processes: list[ProcessEstimate],
    ) -> list[AdditionalCostItem]:
        """BOMからの部品費を追加コストとして計算"""
        costs: list[AdditionalCostItem] = []

        if hearing.bom:
            total_bom_cost = sum(
                int(item.get("unit_price", 0) * item.get("quantity", 1))
                for item in hearing.bom
            )
            if total_bom_cost > 0:
                costs.append(AdditionalCostItem(
                    cost_type="bom_components",
                    description=f"部品費（BOM {len(hearing.bom)}点）",
                    amount=total_bom_cost,
                    per_piece=True,
                    confidence=0.7,
                ))
        else:
            # BOMがない場合は概算
            costs.append(AdditionalCostItem(
                cost_type="bom_components",
                description="部品費（BOM未提供のため概算）",
                amount=2000,
                per_piece=True,
                confidence=0.3,
            ))

        # 基板製作費
        costs.append(AdditionalCostItem(
            cost_type="pcb_tooling",
            description="基板製作費（外注）",
            amount=500,
            per_piece=True,
            confidence=0.4,
        ))

        return costs

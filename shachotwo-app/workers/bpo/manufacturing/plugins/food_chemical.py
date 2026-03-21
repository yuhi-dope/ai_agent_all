"""食品・化学プラグイン — 配合展開の計算ロジック"""
from __future__ import annotations

from typing import Any

from workers.bpo.manufacturing.models import (
    AdditionalCostItem,
    CustomerOverrides,
    HearingInput,
    ProcessEstimate,
)
from workers.bpo.manufacturing.plugins.base import ManufacturingPlugin


class FoodChemicalPlugin(ManufacturingPlugin):
    """
    食品・化学（中分類09-10, 16）用プラグイン。

    金属加工と異なる点:
    - レシピ/配合表から原料量を逆算する（配合展開）
    - バッチプロセス（連続生産ではなくバッチ単位）
    - 工程がライン型（計量→混合→加熱→充填→殺菌→包装）
    """

    @property
    def sub_industry_id(self) -> str:
        return "food_chemical"

    @property
    def display_name(self) -> str:
        return "食品・化学"

    @property
    def jsic_codes(self) -> list[str]:
        return ["E-09", "E-10", "E-16"]

    async def estimate_processes(
        self,
        hearing: HearingInput,
        yaml_config: dict[str, Any] | None,
        customer: CustomerOverrides,
    ) -> list[ProcessEstimate]:
        """食品・化学の工程推定"""
        processes: list[ProcessEstimate] = []
        order = 1
        batch_kg = hearing.batch_size_kg or 100.0

        # 計量
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="原料計量",
            equipment="計量台",
            equipment_type="weighing",
            setup_time_min=15,
            cycle_time_min=0.5,
            confidence=0.6,
            notes=f"バッチサイズ {batch_kg}kg",
        ))
        order += 1

        # 混合・撹拌
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="混合・撹拌",
            equipment="撹拌機",
            equipment_type="mixing",
            setup_time_min=30,
            cycle_time_min=0,  # バッチ処理
            confidence=0.5,
            notes=f"バッチ {batch_kg}kg × 撹拌時間は製品により異なる",
        ))
        order += 1

        # 加熱・反応（化学の場合）
        if hearing.sub_industry == "food_chemical" or "加熱" in hearing.specification:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="加熱・反応",
                equipment="反応釜",
                equipment_type="reactor",
                setup_time_min=30,
                cycle_time_min=0,
                confidence=0.5,
                notes="反応条件は製品依存",
            ))
            order += 1

        # 充填・包装
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="充填・包装",
            equipment="充填機",
            equipment_type="filling",
            setup_time_min=30,
            cycle_time_min=0.1,  # 1個あたり6秒
            confidence=0.6,
        ))
        order += 1

        # 品質検査
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="品質検査",
            equipment="検査",
            equipment_type="inspection",
            setup_time_min=15,
            cycle_time_min=0.5,
            confidence=0.7,
        ))

        return processes

    def calculate_additional_costs(
        self,
        hearing: HearingInput,
        processes: list[ProcessEstimate],
    ) -> list[AdditionalCostItem]:
        """配合ロス（歩留まり損失）を追加コストとして計算"""
        costs: list[AdditionalCostItem] = []

        # 配合ロスは一般的に3-5%
        loss_rate = 0.05
        batch_kg = hearing.batch_size_kg or 100.0
        estimated_material_cost_per_kg = 500  # デフォルト概算

        loss_cost = int(batch_kg * estimated_material_cost_per_kg * loss_rate)
        if loss_cost > 0:
            costs.append(AdditionalCostItem(
                cost_type="recipe_loss",
                description=f"配合ロス（歩留まり {int((1 - loss_rate) * 100)}%）",
                amount=loss_cost,
                per_piece=False,
                confidence=0.4,
            ))

        return costs

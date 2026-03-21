"""樹脂成型プラグイン — 金型償却の計算ロジック"""
from __future__ import annotations

from typing import Any

from workers.bpo.manufacturing.models import (
    AdditionalCostItem,
    CustomerOverrides,
    HearingInput,
    ProcessEstimate,
)
from workers.bpo.manufacturing.plugins.base import ManufacturingPlugin


class PlasticsPlugin(ManufacturingPlugin):
    """
    樹脂成型（中分類18-19）用プラグイン。

    金属加工と異なる点:
    - 金型費の償却計算（金型代 ÷ 生産予定数量 = 個あたり金型償却費）
    - 射出成型機はトン数で単価が異なる
    - 成形サイクルは秒単位（金属加工の分単位と異なる）
    """

    @property
    def sub_industry_id(self) -> str:
        return "plastics"

    @property
    def display_name(self) -> str:
        return "プラスチック・ゴム成型"

    @property
    def jsic_codes(self) -> list[str]:
        return ["E-18", "E-19"]

    async def estimate_processes(
        self,
        hearing: HearingInput,
        yaml_config: dict[str, Any] | None,
        customer: CustomerOverrides,
    ) -> list[ProcessEstimate]:
        """樹脂成型の工程推定"""
        processes: list[ProcessEstimate] = []
        order = 1

        # 金型設計・製作（初回のみだが工程としてリスト）
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="金型設計・製作",
            equipment="外注",
            equipment_type="mold_fabrication",
            setup_time_min=0,
            cycle_time_min=0,
            is_outsource=True,
            confidence=0.5,
            notes="金型費は別途。償却計算で按分",
        ))
        order += 1

        # 材料乾燥（吸湿性樹脂の場合）
        material = hearing.material.upper()
        if material in ("PA", "PA6", "PA66", "PET", "PC", "PMMA", "ABS"):
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="材料乾燥",
                equipment="除湿乾燥機",
                equipment_type="dryer",
                setup_time_min=10,
                cycle_time_min=0,  # バッチ処理のため個あたりは実質0
                confidence=0.7,
                notes=f"{material}は吸湿性のため乾燥が必要",
            ))
            order += 1

        # 射出成型
        eq_config = {}
        if yaml_config and "equipment" in yaml_config:
            eq_config = yaml_config["equipment"].get("injection_molding", {})

        charge_rate = customer.charge_rates.get(
            "射出成型機",
            eq_config.get("charge_rate_yen_hour", 6000),
        )
        # 成型サイクルは秒単位（30-120秒が一般的）→ 分に換算
        cycle_sec = 45  # デフォルト45秒
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="射出成型",
            equipment="射出成型機",
            equipment_type="injection_molding",
            setup_time_min=60,
            cycle_time_min=round(cycle_sec / 60, 2),
            confidence=0.6,
            notes=f"成型サイクル {cycle_sec}秒想定",
        ))
        order += 1

        # ゲートカット・バリ取り
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="ゲートカット・バリ取り",
            equipment="手作業",
            equipment_type="deburring",
            setup_time_min=5,
            cycle_time_min=1,
            confidence=0.7,
        ))
        order += 1

        # 検査
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="検査",
            equipment="検査",
            equipment_type="inspection",
            setup_time_min=10,
            cycle_time_min=1,
            confidence=0.8,
        ))

        return processes

    def calculate_additional_costs(
        self,
        hearing: HearingInput,
        processes: list[ProcessEstimate],
    ) -> list[AdditionalCostItem]:
        """金型償却費を追加コストとして計算"""
        costs: list[AdditionalCostItem] = []

        # 金型費の推定（サイズ・材質で大きく変わる。ここは概算）
        mold_cost = 500_000  # デフォルト50万円

        # 生産予定数量で償却
        expected_lifetime = max(hearing.quantity, 10_000)  # 最低1万個で償却
        per_piece_amortization = int(mold_cost / expected_lifetime)

        costs.append(AdditionalCostItem(
            cost_type="mold_amortization",
            description=f"金型償却費（金型費 ¥{mold_cost:,} ÷ {expected_lifetime:,}個）",
            amount=per_piece_amortization,
            per_piece=True,
            confidence=0.4,
        ))

        return costs

"""製造業見積プラグイン基底クラス"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from workers.bpo.manufacturing.models import (
    AdditionalCostItem,
    CustomerOverrides,
    HearingInput,
    ProcessEstimate,
)


class ManufacturingPlugin(ABC):
    """
    Layer 2 Pythonプラグインの基底クラス。

    YAMLだけでは表現できない「計算構造の違い」を吸収する。
    例: 樹脂成型の金型償却、食品の配合展開、電子部品のBOM展開
    """

    @property
    @abstractmethod
    def sub_industry_id(self) -> str:
        """プラグインID（例: 'plastics', 'food_chemical', 'electronics'）"""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """表示名（例: '樹脂成型', '食品・化学', '電子部品'）"""

    @property
    @abstractmethod
    def jsic_codes(self) -> list[str]:
        """対応するJSIC中分類コード"""

    def can_estimate_processes(self, hearing: HearingInput) -> bool:
        """このプラグインが工程推定を担当できるか。デフォルトTrue"""
        return True

    @abstractmethod
    async def estimate_processes(
        self,
        hearing: HearingInput,
        yaml_config: dict[str, Any] | None,
        customer: CustomerOverrides,
    ) -> list[ProcessEstimate]:
        """工程推定。プラグイン固有ロジック"""

    def calculate_additional_costs(
        self,
        hearing: HearingInput,
        processes: list[ProcessEstimate],
    ) -> list[AdditionalCostItem]:
        """プラグイン固有の追加コスト。デフォルトは空"""
        return []

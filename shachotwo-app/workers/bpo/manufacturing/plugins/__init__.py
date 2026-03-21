"""製造業見積プラグインレジストリ"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workers.bpo.manufacturing.plugins.base import ManufacturingPlugin

logger = logging.getLogger(__name__)

_PLUGIN_REGISTRY: dict[str, "ManufacturingPlugin"] = {}
_loaded = False


def register_plugin(plugin: "ManufacturingPlugin") -> None:
    """プラグインをレジストリに登録する"""
    _PLUGIN_REGISTRY[plugin.sub_industry_id] = plugin
    logger.info(f"Manufacturing plugin registered: {plugin.sub_industry_id}")


def get_plugin(sub_industry: str) -> "ManufacturingPlugin | None":
    """sub_industryに対応するプラグインを取得。なければNone"""
    if not _loaded:
        _load_plugins()
    return _PLUGIN_REGISTRY.get(sub_industry)


def list_plugins() -> list[str]:
    """登録済みプラグインID一覧"""
    if not _loaded:
        _load_plugins()
    return list(_PLUGIN_REGISTRY.keys())


def _load_plugins() -> None:
    """plugins/ 配下の全プラグインを自動登録"""
    global _loaded
    _loaded = True
    try:
        from workers.bpo.manufacturing.plugins.plastics import PlasticsPlugin
        register_plugin(PlasticsPlugin())
    except ImportError:
        pass
    try:
        from workers.bpo.manufacturing.plugins.food_chemical import FoodChemicalPlugin
        register_plugin(FoodChemicalPlugin())
    except ImportError:
        pass
    try:
        from workers.bpo.manufacturing.plugins.electronics import ElectronicsPlugin
        register_plugin(ElectronicsPlugin())
    except ImportError:
        pass

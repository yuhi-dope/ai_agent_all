"""SaaS MCP アダプタのレジストリ（後方互換ラッパー）.

v5.0 で server/saas/tools/ に移行済み。
main.py 等の既存コードが呼ぶ関数をここで維持する。
内部では ToolRegistry に委譲しつつ、旧アダプタクラスもフォールバックとして保持。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.saas.mcp.base import SaaSMCPAdapter

logger = logging.getLogger(__name__)

# 旧アダプタクラスのマップ（フォールバック用）
_ADAPTER_REGISTRY: dict[str, type[SaaSMCPAdapter]] = {}
_discovered = False


def register_adapter(cls: type[SaaSMCPAdapter]) -> type[SaaSMCPAdapter]:
    """旧: SaaS MCP アダプタをレジストリに登録するデコレータ.

    旧アダプタの @register_adapter は引き続き動作する（後方互換）。
    """
    if not cls.saas_name:
        raise ValueError(f"{cls.__name__} に saas_name が設定されていません")
    _ADAPTER_REGISTRY[cls.saas_name] = cls
    logger.debug("旧アダプタ登録: %s (%s)", cls.saas_name, cls.__name__)
    return cls


def _discover_adapters() -> None:
    """server/saas/mcp/ 配下のモジュールを自動インポートしてアダプタを検出する."""
    global _discovered
    if _discovered:
        return

    import server.saas.mcp as package

    for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
        if modname in ("base", "registry", "__init__"):
            continue
        try:
            importlib.import_module(f"server.saas.mcp.{modname}")
        except Exception:
            logger.warning("SaaS モジュール '%s' のロードに失敗", modname, exc_info=True)

    _discovered = True


def get_adapter(saas_name: str) -> SaaSMCPAdapter | None:
    """SaaS名からアダプタインスタンスを取得する（旧互換）."""
    _discover_adapters()
    cls = _ADAPTER_REGISTRY.get(saas_name)
    if cls is None:
        return None
    return cls()


def get_adapter_class(saas_name: str) -> type[SaaSMCPAdapter] | None:
    """SaaS名からアダプタクラスを取得する（旧互換）."""
    _discover_adapters()
    return _ADAPTER_REGISTRY.get(saas_name)


def get_all_adapters() -> dict[str, type[SaaSMCPAdapter]]:
    """全登録済みアダプタクラスを返す."""
    _discover_adapters()
    return dict(_ADAPTER_REGISTRY)


def get_adapters_by_genre(genre: str) -> dict[str, type[SaaSMCPAdapter]]:
    """ジャンルでフィルタしたアダプタクラスを返す."""
    _discover_adapters()
    return {
        name: cls
        for name, cls in _ADAPTER_REGISTRY.items()
        if cls.genre == genre
    }


def list_supported_saas() -> list[dict]:
    """対応SaaS一覧をAPI レスポンス用に返す.

    新しい ToolRegistry を優先し、旧アダプタも含める。
    """
    from server.saas.tools.registry import ToolRegistry

    # 新 ToolRegistry から取得
    result = ToolRegistry.list_saas()
    seen = {r["saas_name"] for r in result}

    # 旧アダプタでまだ新ToolRegistryに登録されていないものをフォールバック
    _discover_adapters()
    for cls in _ADAPTER_REGISTRY.values():
        if cls.saas_name not in seen:
            adapter = cls()
            result.append(adapter.to_dict())

    return result

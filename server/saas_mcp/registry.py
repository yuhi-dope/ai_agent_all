"""SaaS MCP アダプタのレジストリ.

アダプタの自動登録・検索を行う。
新しいSaaSを追加する場合は @register_adapter デコレータを使用する。

使用例:
    from server.saas_mcp.registry import get_adapter, get_all_adapters

    # 特定のSaaSアダプタを取得
    adapter = get_adapter("salesforce")

    # 全アダプタを取得
    all_adapters = get_all_adapters()

    # ジャンルでフィルタ
    sfa_adapters = get_adapters_by_genre("sfa")
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.saas_mcp.base import SaaSMCPAdapter

logger = logging.getLogger(__name__)

# 登録済みアダプタクラスのマップ: saas_name → adapter_class
_ADAPTER_REGISTRY: dict[str, type[SaaSMCPAdapter]] = {}

# 自動検出済みフラグ
_discovered = False


def register_adapter(cls: type[SaaSMCPAdapter]) -> type[SaaSMCPAdapter]:
    """SaaS MCP アダプタをレジストリに登録するデコレータ.

    使用例:
        @register_adapter
        class SalesforceAdapter(SaaSMCPAdapter):
            saas_name = "salesforce"
            genre = "sfa"
            ...
    """
    if not cls.saas_name:
        raise ValueError(f"{cls.__name__} に saas_name が設定されていません")

    if cls.saas_name in _ADAPTER_REGISTRY:
        logger.warning(
            "アダプタ '%s' は既に登録済み（%s → %s）。上書きします",
            cls.saas_name,
            _ADAPTER_REGISTRY[cls.saas_name].__name__,
            cls.__name__,
        )

    _ADAPTER_REGISTRY[cls.saas_name] = cls
    logger.debug("SaaS アダプタ登録: %s (%s)", cls.saas_name, cls.__name__)
    return cls


def _discover_adapters() -> None:
    """server/saas_mcp/ 配下のモジュールを自動インポートしてアダプタを検出する."""
    global _discovered
    if _discovered:
        return

    import server.saas_mcp as package

    for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
        if modname in ("base", "registry", "__init__"):
            continue
        try:
            importlib.import_module(f"server.saas_mcp.{modname}")
            logger.debug("SaaS モジュール検出: %s", modname)
        except Exception:
            logger.warning("SaaS モジュール '%s' のロードに失敗", modname, exc_info=True)

    _discovered = True


def get_adapter(saas_name: str) -> SaaSMCPAdapter | None:
    """SaaS名からアダプタインスタンスを取得する.

    Args:
        saas_name: SaaS名（'salesforce', 'freee' 等）

    Returns:
        アダプタインスタンス。見つからない場合はNone
    """
    _discover_adapters()
    cls = _ADAPTER_REGISTRY.get(saas_name)
    if cls is None:
        return None
    return cls()


def get_adapter_class(saas_name: str) -> type[SaaSMCPAdapter] | None:
    """SaaS名からアダプタクラスを取得する."""
    _discover_adapters()
    return _ADAPTER_REGISTRY.get(saas_name)


def get_all_adapters() -> dict[str, type[SaaSMCPAdapter]]:
    """全登録済みアダプタクラスを返す.

    Returns:
        saas_name → adapter_class のマッピング
    """
    _discover_adapters()
    return dict(_ADAPTER_REGISTRY)


def get_adapters_by_genre(genre: str) -> dict[str, type[SaaSMCPAdapter]]:
    """ジャンルでフィルタしたアダプタクラスを返す.

    Args:
        genre: ジャンル名（'sfa', 'accounting' 等）

    Returns:
        該当ジャンルのアダプタクラスマッピング
    """
    _discover_adapters()
    return {
        name: cls
        for name, cls in _ADAPTER_REGISTRY.items()
        if cls.genre == genre
    }


def list_supported_saas() -> list[dict]:
    """対応SaaS一覧をAPI レスポンス用に返す."""
    _discover_adapters()
    result = []
    for cls in _ADAPTER_REGISTRY.values():
        adapter = cls()
        result.append(adapter.to_dict())
    return result

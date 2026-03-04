"""SaaS MCP アダプタパッケージ（後方互換ラッパー）.

v5.0 で server/saas/tools/ に移行済み。
このモジュールは既存コード（main.py, executor.py 等）からの import を壊さないための互換レイヤー。

新規コードは server.saas.tools を直接使うこと。
"""

from server.saas.mcp.base import (
    AuthMethod,
    ConnectionStatus,
    SaaSCredentials,
    SaaSMCPAdapter,
    SaaSToolInfo,
)
from server.saas.mcp.registry import (
    get_adapter,
    get_adapter_class,
    get_adapters_by_genre,
    get_all_adapters,
    list_supported_saas,
    register_adapter,
)

__all__ = [
    "AuthMethod",
    "ConnectionStatus",
    "SaaSCredentials",
    "SaaSMCPAdapter",
    "SaaSToolInfo",
    "get_adapter",
    "get_adapter_class",
    "get_adapters_by_genre",
    "get_all_adapters",
    "list_supported_saas",
    "register_adapter",
]

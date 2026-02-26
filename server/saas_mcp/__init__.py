"""SaaS MCP アダプタパッケージ.

既存SaaS（Salesforce/freee/Slack/Google Workspace/kintone/SmartHR等）に
MCP経由で接続し、AI社員として業務を代行するためのアダプタ群。

使用例:
    from server.saas_mcp import get_adapter, list_supported_saas

    # 特定のSaaSアダプタを取得
    adapter = get_adapter("salesforce")
    await adapter.connect(credentials)
    tools = await adapter.get_available_tools()

    # 対応SaaS一覧
    saas_list = list_supported_saas()
"""

from server.saas_mcp.base import (
    AuthMethod,
    ConnectionStatus,
    SaaSCredentials,
    SaaSMCPAdapter,
    SaaSToolInfo,
)
from server.saas_mcp.registry import (
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

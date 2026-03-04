"""Typed Function Tools — SaaS ツール基盤.

@saas_tool デコレータで関数を登録し、ToolRegistry 経由で実行する。
旧 SaaSMCPAdapter クラス階層を置き換える。

使用例:
    from server.saas.tools import ToolRegistry

    # ツール一覧
    tools = ToolRegistry.list_tools("kintone")

    # 実行
    result = await ToolRegistry.execute(
        "kintone_add_record",
        {"app_id": 1, "record": {...}},
        credentials=creds,
    )
"""

from server.saas.tools.registry import ToolRegistry, saas_tool

__all__ = ["ToolRegistry", "saas_tool"]

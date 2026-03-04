"""ToolRegistry — @saas_tool デコレータ + 自動登録レジストリ.

旧 SaaSMCPAdapter クラス階層を置き換え、
関数ベースの SaaS ツールを登録・検索・実行する。
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ToolInfo:
    """登録済みツールのメタ情報."""

    name: str
    description: str
    saas_name: str
    genre: str
    parameters: dict[str, str]
    fn: Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class SaaSMetadata:
    """SaaS のメタ情報（旧 SaaSMCPAdapter のクラス属性に相当）."""

    saas_name: str
    display_name: str
    genre: str
    description: str
    supported_auth_methods: list[str] = field(default_factory=list)
    default_scopes: list[str] = field(default_factory=list)
    # 接続・ヘルスチェック・OAuthなどの関数
    connect_fn: Callable | None = None
    health_check_fn: Callable | None = None
    oauth_authorize_url_fn: Callable | None = None
    schema_fn: Callable | None = None


# --- グローバルレジストリ ---
_TOOL_REGISTRY: dict[str, ToolInfo] = {}
_SAAS_METADATA: dict[str, SaaSMetadata] = {}
_discovered = False


def saas_tool(
    *,
    saas: str,
    genre: str,
    description: str | None = None,
):
    """SaaS ツール関数を登録するデコレータ.

    使用例::

        @saas_tool(saas="kintone", genre="admin")
        async def kintone_add_record(app_id: int, record: dict, *, creds) -> dict:
            \"\"\"kintone アプリにレコードを1件追加する\"\"\"
            ...

    - 関数名がそのままツール名になる
    - docstring が description になる（引数で上書き可）
    - 型ヒントから parameters を自動生成
    - creds は ToolRegistry.execute() が自動注入する
    """

    def decorator(fn: Callable[..., Awaitable[dict[str, Any]]]):
        tool_name = fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip().split("\n")[0]

        # 型ヒントから parameters を生成
        sig = inspect.signature(fn)
        params: dict[str, str] = {}
        for pname, param in sig.parameters.items():
            if pname == "creds":
                continue  # credentials は自動注入
            ann = param.annotation
            if ann is inspect.Parameter.empty:
                params[pname] = "any"
            else:
                type_name = getattr(ann, "__name__", str(ann))
                # Optional 判定
                if param.default is not inspect.Parameter.empty:
                    type_name += " (optional)"
                params[pname] = type_name

        info = ToolInfo(
            name=tool_name,
            description=tool_desc,
            saas_name=saas,
            genre=genre,
            parameters=params,
            fn=fn,
        )

        if tool_name in _TOOL_REGISTRY:
            logger.warning("ツール '%s' は既に登録済み。上書きします", tool_name)
        _TOOL_REGISTRY[tool_name] = info
        logger.debug("SaaS ツール登録: %s (saas=%s)", tool_name, saas)
        return fn

    return decorator


def register_saas(metadata: SaaSMetadata) -> None:
    """SaaS メタ情報を登録する."""
    _SAAS_METADATA[metadata.saas_name] = metadata
    logger.debug("SaaS メタ登録: %s", metadata.saas_name)


def _discover_tools() -> None:
    """server/saas/tools/ 配下のモジュールを自動インポートしてツールを検出する."""
    global _discovered
    if _discovered:
        return

    import server.saas.tools as package

    for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
        if modname in ("registry", "__init__"):
            continue
        try:
            importlib.import_module(f"server.saas.tools.{modname}")
            logger.debug("SaaS ツールモジュール検出: %s", modname)
        except Exception:
            logger.warning(
                "SaaS ツールモジュール '%s' のロードに失敗", modname, exc_info=True
            )

    _discovered = True


class ToolRegistry:
    """SaaS ツールの検索・実行を行う静的レジストリ."""

    @staticmethod
    def list_tools(saas_name: str | None = None) -> list[ToolInfo]:
        """ツール一覧を返す. saas_name を指定するとフィルタ."""
        _discover_tools()
        tools = list(_TOOL_REGISTRY.values())
        if saas_name:
            tools = [t for t in tools if t.saas_name == saas_name]
        return tools

    @staticmethod
    def get_tool(tool_name: str) -> ToolInfo | None:
        """ツール名からツール情報を取得."""
        _discover_tools()
        return _TOOL_REGISTRY.get(tool_name)

    @staticmethod
    async def execute(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        creds: Any = None,
    ) -> dict[str, Any]:
        """ツールを実行する. creds は自動注入."""
        _discover_tools()
        info = _TOOL_REGISTRY.get(tool_name)
        if not info:
            raise ValueError(f"不明なツール: '{tool_name}'")

        # creds を kwargs として注入
        sig = inspect.signature(info.fn)
        kwargs: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if pname == "creds":
                kwargs["creds"] = creds
            elif pname in arguments:
                kwargs[pname] = arguments[pname]
            elif param.default is not inspect.Parameter.empty:
                pass  # デフォルト値を使う
            else:
                raise ValueError(f"ツール '{tool_name}' の必須引数 '{pname}' が不足")

        return await info.fn(**kwargs)

    @staticmethod
    def list_saas() -> list[dict[str, Any]]:
        """対応SaaS一覧をAPI レスポンス用に返す."""
        _discover_tools()
        result = []
        for meta in _SAAS_METADATA.values():
            result.append({
                "saas_name": meta.saas_name,
                "display_name": meta.display_name,
                "genre": meta.genre,
                "supported_auth_methods": meta.supported_auth_methods,
                "default_scopes": meta.default_scopes,
                "description": meta.description,
                "status": "disconnected",
            })
        return result

    @staticmethod
    def get_saas_metadata(saas_name: str) -> SaaSMetadata | None:
        """SaaS メタ情報を取得."""
        _discover_tools()
        return _SAAS_METADATA.get(saas_name)

    @staticmethod
    def list_tools_by_genre(genre: str) -> list[ToolInfo]:
        """ジャンルでフィルタしたツール一覧."""
        _discover_tools()
        return [t for t in _TOOL_REGISTRY.values() if t.genre == genre]

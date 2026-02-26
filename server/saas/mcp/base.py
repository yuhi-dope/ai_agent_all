"""SaaS MCP アダプタの抽象基底クラス.

全SaaS MCP アダプタが共通で実装すべきインターフェースを定義する。
新しいSaaSを追加する場合は、このクラスを継承して具体的な接続ロジックを実装する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuthMethod(str, Enum):
    """認証方式."""

    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BASIC = "basic"


class ConnectionStatus(str, Enum):
    """接続ステータス."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    TOKEN_EXPIRED = "token_expired"
    ERROR = "error"


@dataclass
class SaaSCredentials:
    """SaaS接続に必要な認証情報."""

    auth_method: AuthMethod
    access_token: str | None = None
    refresh_token: str | None = None
    api_key: str | None = None
    instance_url: str | None = None
    scopes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SaaSToolInfo:
    """MCPツールの情報."""

    name: str
    description: str
    parameters: dict[str, Any]
    genre: str
    saas_name: str


class SaaSMCPAdapter(ABC):
    """全SaaS MCPアダプタの抽象基底クラス.

    新しいSaaSを追加する場合:
    1. server/saas_mcp/{saas_name}.py を作成
    2. このクラスを継承
    3. @register_adapter デコレータを付与
    4. 抽象メソッドを実装
    """

    # サブクラスで定義すべきクラス属性
    saas_name: str = ""
    display_name: str = ""
    genre: str = ""
    supported_auth_methods: list[AuthMethod] = []
    default_scopes: list[str] = []
    mcp_server_type: str = ""  # 'official', 'community', 'custom'
    description: str = ""

    def __init__(self) -> None:
        self._status = ConnectionStatus.DISCONNECTED
        self._credentials: SaaSCredentials | None = None
        self._access_token: str | None = None
        self._instance_url: str | None = None

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    # --- 共通 HTTP ヘルパー ---

    async def _api_request(self, method: str, url: str, **kwargs) -> dict:
        """認証ヘッダー付きHTTPリクエスト."""
        import httpx

        headers = kwargs.pop("headers", {})
        headers.setdefault("Authorization", f"Bearer {self._access_token}")
        headers.setdefault("Content-Type", "application/json")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204:
                return {"success": True}
            return resp.json()

    # --- 接続管理 ---

    @abstractmethod
    async def connect(self, credentials: SaaSCredentials) -> None:
        """SaaSに接続する.

        Args:
            credentials: 認証情報
        Raises:
            ConnectionError: 接続失敗時
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """SaaSから切断する."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """接続が有効か確認する.

        Returns:
            True: 接続有効, False: 接続無効（トークン切れ等）
        """
        ...

    # --- MCP ツール ---

    @abstractmethod
    async def get_available_tools(self) -> list[SaaSToolInfo]:
        """利用可能なMCPツール一覧を返す.

        Returns:
            MCPツール情報のリスト
        """
        ...

    @abstractmethod
    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """MCPツールを実行する.

        Args:
            tool_name: ツール名
            arguments: ツール引数
        Returns:
            実行結果
        Raises:
            ValueError: 不明なツール名
            RuntimeError: 実行エラー
        """
        ...

    # --- スキーマ取得（Phase 2 構造学習用）---

    @abstractmethod
    async def get_schema(self) -> dict[str, Any]:
        """SaaSのデータ構造（オブジェクト・フィールド・リレーション）を返す.

        Phase 2 の構造学習で使用。client_schema_snapshots に保存される。

        Returns:
            スキーマ情報（オブジェクト名、フィールド定義、リレーション等）
        """
        ...

    # --- OAuth ヘルパー ---

    def get_oauth_authorize_url(self, redirect_uri: str, state: str) -> str | None:
        """OAuth認可URLを生成する.

        OAuth非対応のアダプタはNoneを返す。

        Args:
            redirect_uri: コールバックURL
            state: CSRF防止用state値
        Returns:
            認可URL。OAuth非対応の場合はNone
        """
        return None

    async def refresh_token(self) -> SaaSCredentials | None:
        """OAuthトークンをリフレッシュする.

        Returns:
            更新された認証情報。リフレッシュ不要/不可の場合はNone
        """
        return None

    # --- ユーティリティ ---

    def to_dict(self) -> dict[str, Any]:
        """アダプタ情報を辞書として返す（API レスポンス用）."""
        return {
            "saas_name": self.saas_name,
            "display_name": self.display_name,
            "genre": self.genre,
            "supported_auth_methods": [m.value for m in self.supported_auth_methods],
            "default_scopes": self.default_scopes,
            "mcp_server_type": self.mcp_server_type,
            "description": self.description,
            "status": self._status.value,
        }

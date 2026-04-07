"""BaseConnector — 全コネクタの抽象基底クラス。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectorConfig:
    tool_name: str = ""
    credentials: dict = field(default_factory=dict)
    connector_type: str = ""
    company_id: str = ""


class BaseConnector(ABC):
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """SaaS からレコードを読み取る。

        Args:
            resource: リソース識別子（app_id, エンドポイント名 等）
            filters:  絞り込み条件（任意）

        Returns:
            レコードの list
        """
        ...

    @abstractmethod
    async def write_record(self, resource: str, data: dict) -> dict:
        """SaaS にレコードを書き込む（新規または更新）。

        Args:
            resource: リソース識別子
            data:     書き込むデータ。id キーが含まれる場合は更新、なければ新規

        Returns:
            SaaS APIのレスポンス dict
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """認証・疎通確認。

        Returns:
            True: 正常接続, False: 接続失敗
        """
        ...

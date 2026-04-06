"""
salesパイプラインテスト用 conftest。
weasyprint 等の重量依存ライブラリをインポート前にモックする。
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub_module(name: str) -> MagicMock:
    """sys.modules に存在しないモジュールをスタブに差し替える。"""
    if name not in sys.modules:
        mock = MagicMock(spec=ModuleType(name))
        sys.modules[name] = mock
    return sys.modules[name]


# weasyprint と依存ライブラリをスタブ化（テスト環境ではインストール不要）
_weasyprint = _stub_module("weasyprint")
_weasyprint.HTML = MagicMock()
_weasyprint.CSS = MagicMock()

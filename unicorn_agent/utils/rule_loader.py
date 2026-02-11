"""ルールファイルの読み込み。rules_dir 内の .md が無い場合は default を返す。"""
from __future__ import annotations

from pathlib import Path


def load_rule(rules_dir: Path, name: str, default: str) -> str:
    """
    rules_dir / "{name}.md" が存在すればその内容を返す。存在しなければ default を返す。
    エンコーディングは UTF-8。読み込み失敗時は default にフォールバック。
    """
    path = rules_dir / f"{name}.md"
    if not path.is_file():
        return default
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default

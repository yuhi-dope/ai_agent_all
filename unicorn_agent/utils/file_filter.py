"""20KB 超・除外パターンに該当するファイルをフィルタ。Coder Agent のコンテキスト用。"""
from __future__ import annotations

import os
from pathlib import Path
from fnmatch import fnmatch

from unicorn_agent.config import FILE_SIZE_LIMIT_BYTES, EXCLUDED_FILE_PATTERNS


def _is_excluded(path: str) -> bool:
    """除外パターンにマッチするか。"""
    name = os.path.basename(path)
    for pattern in EXCLUDED_FILE_PATTERNS:
        if pattern.startswith("*"):
            if fnmatch(name, pattern):
                return True
        elif name == pattern or pattern in path:
            return True
    return False


def filter_readable_files(
    base_dir: str | Path,
    *,
    size_limit: int = FILE_SIZE_LIMIT_BYTES,
) -> list[str]:
    """
    読み込んでよいファイルの相対パス一覧を返す。
    - サイズが size_limit 以下
    - EXCLUDED_FILE_PATTERNS にマッチしない
    """
    base = Path(base_dir)
    if not base.is_dir():
        return []

    skip_dirs = {"node_modules", "__pycache__", ".git", ".venv", "venv"}
    allowed: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel_root = os.path.relpath(root, base)
        if rel_root == ".":
            rel_root = ""
        for f in files:
            rel_path = os.path.join(rel_root, f) if rel_root else f
            if _is_excluded(rel_path):
                continue
            full = base / rel_path
            try:
                if full.is_file() and full.stat().st_size <= size_limit:
                    allowed.append(rel_path.replace("\\", "/"))
            except OSError:
                continue
    return sorted(allowed)

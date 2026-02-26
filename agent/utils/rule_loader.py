"""ルールファイルの読み込み。rules_dir 内の .md が無い場合は default を返す。"""
from __future__ import annotations

from pathlib import Path

# Genre Classifier が使用する有効なジャンルID一覧
VALID_GENRES = frozenset([
    "sfa", "crm", "accounting", "legal", "admin",
    "it", "marketing", "design", "ma", "no2",
])


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


def load_genre_rules(rules_dir: Path, genre: str) -> str:
    """
    ジャンル専門ルールを読み込む。
    rules_dir / "genre" / "{genre}_rules.md" が存在すればその内容を返す。
    genre_dir が rules_dir の直下にない場合は親ディレクトリから探す。
    """
    if not genre or genre not in VALID_GENRES:
        return ""
    # まず rules_dir / "genre" を試す
    genre_dir = rules_dir / "genre"
    if not genre_dir.is_dir():
        # rules/develop/ の場合は rules/genre/ を探す
        genre_dir = rules_dir.parent / "genre"
    return load_rule(genre_dir, f"{genre}_rules", "")


def load_genre_db_schema(rules_dir: Path, genre: str) -> str:
    """
    ジャンル専門DBスキーマテンプレートを読み込む。
    rules_dir / "genre" / "{genre}_db_schema.md" が存在すればその内容を返す。
    """
    if not genre or genre not in VALID_GENRES:
        return ""
    genre_dir = rules_dir / "genre"
    if not genre_dir.is_dir():
        genre_dir = rules_dir.parent / "genre"
    return load_rule(genre_dir, f"{genre}_db_schema", "")

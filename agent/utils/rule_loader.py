"""ルールファイルの読み込み。rules_dir 内の .md が無い場合は default を返す。"""
from __future__ import annotations

from pathlib import Path

# Genre Classifier が使用する有効なジャンルID一覧
VALID_GENRES = frozenset([
    "sfa", "crm", "accounting", "legal", "admin",
    "it", "marketing", "design", "ma", "no2",
])

# BPO genre ID → 日本語名マッピング（rules/saas/genre/ のファイル名に使用）
GENRE_TO_JAPANESE: dict[str, str] = {
    "sfa": "営業",
    "crm": "顧客管理",
    "accounting": "会計",
    "legal": "法務",
    "admin": "総務",
    "it": "IT",
    "marketing": "マーケティング",
    "design": "デザイン",
    "ma": "MA",
    "no2": "No2",
    "hr": "人事",
}


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


def load_bpo_rules(rules_dir: Path, saas_name: str, genre: str) -> str:
    """BPO 用のレイヤード・ルールを合成して返す。

    合成順:
      1. general_rules.md (共通ルール)
      2. platform/{saas_name}_rules.md (SaaS固有技術制約)
      3. genre/{日本語genre}_rules.md (業務ドメインルール)
      4. learned/{saas_name}_{日本語genre}_learned.md (学習済みルール)

    各レイヤーが存在しなければスキップする。
    """
    parts: list[str] = []

    # 1. general_rules
    general = load_rule(rules_dir, "general_rules", "")
    if general:
        parts.append(general)

    # 2. platform rules (フォールバック: 旧フラット構造)
    platform_dir = rules_dir / "platform"
    platform_rules = load_rule(platform_dir, f"{saas_name}_rules", "")
    if not platform_rules:
        platform_rules = load_rule(rules_dir, f"{saas_name}_rules", "")
    if platform_rules:
        parts.append(platform_rules)

    # 3. genre rules
    ja_genre = GENRE_TO_JAPANESE.get(genre, "") if genre else ""
    if ja_genre:
        genre_dir = rules_dir / "genre"
        genre_rules = load_rule(genre_dir, f"{ja_genre}_rules", "")
        if genre_rules:
            parts.append(genre_rules)

    # 4. learned rules
    if saas_name and ja_genre:
        learned_dir = rules_dir / "learned"
        learned_rules = load_rule(
            learned_dir, f"{saas_name}_{ja_genre}_learned", ""
        )
        if learned_rules:
            parts.append(learned_rules)

    return "\n\n---\n\n".join(parts)


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

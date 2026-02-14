"""
review 合格 run のルール改善案を rules/*.md に自動追記する。
重複ブロック（同一見出し＋先頭1行一致）はスキップする。
"""

from pathlib import Path


RULE_IMPROVEMENT_KEYS = [
    ("spec_rules_improvement", "spec_rules"),
    ("coder_rules_improvement", "coder_rules"),
    ("review_rules_improvement", "review_rules"),
    ("fix_rules_improvement", "fix_rules"),
    ("pr_rules_improvement", "pr_rules"),
]


def _signature(block: str) -> str:
    """重複検出用: 最初の見出し行と先頭1行を結合した短い文字列を返す。"""
    lines = block.strip().split("\n")[:3]
    return "\n".join(lines).strip()[:200]


def _is_duplicate(existing_content: str, new_block: str) -> bool:
    """既存内容に同じ署名のブロックが含まれていれば True。"""
    sig = _signature(new_block)
    if not sig:
        return False
    parts = existing_content.split("\n\n---\n\n## 自動追加 (run_id:")
    for part in parts[1:]:  # 最初はヘッダなし
        if part.strip().split("\n")[:3] == new_block.strip().split("\n")[:3]:
            return True
        if sig in part[:300]:
            return True
    return False


def merge_improvements_into_rules(
    workspace_root: Path,
    rules_dir_name: str,
    run_id: str,
    result: dict,
    genre: str | None = None,
) -> None:
    """
    result の各 *_rules_improvement を対応する rules/<name>.md の末尾に追記する。
    重複ブロックはスキップ。genre がある場合は追記ヘッダに含める。
    追記形式: \\n\\n---\\n\\n## 自動追加 (run_id: xxx[, genre: 法務])\\n\\n<本文>
    """
    rules_dir = workspace_root / rules_dir_name
    if not rules_dir.is_dir():
        return

    header = f"## 自動追加 (run_id: {run_id}"
    if genre and genre.strip():
        header += f", genre: {genre.strip()}"
    header += ")\n\n"

    for improvement_key, rule_name in RULE_IMPROVEMENT_KEYS:
        content = (result.get(improvement_key) or "").strip()
        if not content:
            continue
        rule_path = rules_dir / f"{rule_name}.md"
        appendix = f"\n\n---\n\n{header}{content}\n"
        if rule_path.exists():
            existing = rule_path.read_text(encoding="utf-8")
            if _is_duplicate(existing, content):
                continue
            rule_path.write_text(existing.rstrip() + appendix, encoding="utf-8")
        else:
            if content.lstrip().startswith("#"):
                rule_path.write_text(content + appendix, encoding="utf-8")
            else:
                rule_path.write_text(f"# {rule_name}\n\n{content}{appendix}", encoding="utf-8")

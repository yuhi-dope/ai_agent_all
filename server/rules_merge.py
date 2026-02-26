"""
ルール改善のレビューワークフロー。
Run 成功時にルール改善案を rule_changes テーブルに pending で保存。
開発者が /admin で承認すると、ルールファイルに追記される。
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

RULE_IMPROVEMENT_KEYS = [
    ("spec_rules_improvement", "spec_rules"),
    ("coder_rules_improvement", "coder_rules"),
    ("review_rules_improvement", "review_rules"),
    ("fix_rules_improvement", "fix_rules"),
    ("publish_rules_improvement", "publish_rules"),
]


def _get_client():
    """Supabase クライアントを返す（シングルトン）。"""
    from server._supabase import get_client

    return get_client()



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
    for part in parts[1:]:
        if part.strip().split("\n")[:3] == new_block.strip().split("\n")[:3]:
            return True
        if sig in part[:300]:
            return True
    return False


def save_pending_improvements(
    run_id: str,
    result: dict,
    genre: str | None = None,
) -> None:
    """
    Run 成功時に各ルール改善案を rule_changes テーブルに status='pending' で保存する。
    ディスクには書き込まない。開発者が承認後に apply_approved_change() で反映。
    """
    client = _get_client()
    if not client:
        return
    for improvement_key, rule_name in RULE_IMPROVEMENT_KEYS:
        content = (result.get(improvement_key) or "").strip()
        if not content:
            continue
        try:
            client.table("rule_changes").insert(
                {
                    "run_id": run_id,
                    "rule_name": rule_name,
                    "added_block": content,
                    "genre": (genre or "").strip() or None,
                    "status": "pending",
                }
            ).execute()
        except Exception:
            pass


def apply_approved_change(
    workspace_root: Path,
    rules_dir_name: str,
    rule_change: dict,
) -> bool:
    """
    承認されたルール改善をルールファイルに追記する。
    rule_change は rule_changes テーブルの1行（dict）。
    重複ブロックはスキップして False を返す。
    """
    rules_dir = workspace_root / rules_dir_name
    if not rules_dir.is_dir():
        return False

    rule_name = rule_change.get("rule_name") or ""
    content = (rule_change.get("added_block") or "").strip()
    run_id = rule_change.get("run_id") or "unknown"
    genre = (rule_change.get("genre") or "").strip()

    if not content or not rule_name:
        return False

    header = f"## 自動追加 (run_id: {run_id}"
    if genre:
        header += f", genre: {genre}"
    header += ")\n\n"

    # saas_ プレフィックスの場合は rules/saas/ に書き出す
    if rule_name.startswith("saas_"):
        saas_specific = rule_name.replace("saas_", "", 1)
        saas_rules_dir = workspace_root / "rules" / "saas"
        saas_rules_dir.mkdir(parents=True, exist_ok=True)
        rule_path = saas_rules_dir / f"{saas_specific}_rules.md"
    else:
        rule_path = rules_dir / f"{rule_name}.md"
    appendix = f"\n\n---\n\n{header}{content}\n"

    if rule_path.exists():
        existing = rule_path.read_text(encoding="utf-8")
        if _is_duplicate(existing, content):
            return False
        rule_path.write_text(existing.rstrip() + appendix, encoding="utf-8")
    else:
        if content.lstrip().startswith("#"):
            rule_path.write_text(content + appendix, encoding="utf-8")
        else:
            rule_path.write_text(f"# {rule_name}\n\n{content}{appendix}", encoding="utf-8")

    return True


# 後方互換: 既存テストやスクリプト用
def merge_improvements_into_rules(
    workspace_root: Path,
    rules_dir_name: str,
    run_id: str,
    result: dict,
    genre: str | None = None,
) -> None:
    """後方互換ラッパー。save_pending_improvements を呼ぶ。"""
    save_pending_improvements(run_id=run_id, result=result, genre=genre)

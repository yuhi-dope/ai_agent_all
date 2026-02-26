"""Fix Agent: error_logs を要約し、Coder 用の修正指示を State に追加。retry_count をインクリメント。"""
from __future__ import annotations

from pathlib import Path

from agent.state import AgentState
from agent.utils.rule_loader import load_rule


def fix_agent_node(state: AgentState) -> dict:
    """error_logs を要約して fix_instruction に載せ、retry_count を 1 増やす。"""
    error_logs = state.get("error_logs") or []
    retry_count = state.get("retry_count") or 0
    workspace_root = state.get("workspace_root") or "."
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = Path(workspace_root) / rules_dir_name
    fix_rules = load_rule(rules_dir, "fix_rules", "")

    error_block = "以下のエラーを修正してください:\n" + "\n".join(
        f"- {e}" for e in error_logs[-10:]
    )
    if fix_rules.strip():
        fix_instruction = (fix_rules.strip() + "\n\n" + error_block).strip()
    else:
        fix_instruction = error_block

    out: dict = {
        "fix_instruction": fix_instruction,
        "retry_count": retry_count + 1,
        "status": "review_ng",
    }
    if state.get("output_rules_improvement"):
        errors_preview = "\n".join(f"- {e[:200]}" for e in error_logs[-5:])
        out["fix_rules_improvement"] = (
            f"# Fix フェーズ 改善・追加ルール案\n\n"
            f"## 今回のエラー要約（直近5件）\n{errors_preview or '(なし)'}\n\n"
            f"## fix_rules.md への追加推奨\n"
            f"上記で繰り返し発生しているパターンがあれば、「よくあるエラーと対処法」として追記してください。\n"
        )
    return out

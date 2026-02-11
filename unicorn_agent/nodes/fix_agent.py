"""Fix Agent: error_logs を要約し、Coder 用の修正指示を State に追加。retry_count をインクリメント。"""

from unicorn_agent.state import AgentState


def fix_agent_node(state: AgentState) -> dict:
    """error_logs を要約して fix_instruction に載せ、retry_count を 1 増やす。"""
    error_logs = state.get("error_logs") or []
    retry_count = state.get("retry_count") or 0

    fix_instruction = "以下のエラーを修正してください:\n" + "\n".join(
        f"- {e}" for e in error_logs[-10:]
    )

    return {
        "fix_instruction": fix_instruction,
        "retry_count": retry_count + 1,
        "status": "review_ng",
    }

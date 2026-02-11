#!/usr/bin/env python3
"""
Unicorn Agent の薄いエントリポイント。
Notion Webhook や Supabase は外側（呼び出し元）で実装し、ここでは graph.invoke のみ行う。
"""

import sys
import os

# プロジェクトルートを path に追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unicorn_agent import initial_state
from unicorn_agent.graph import invoke


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py '<user_requirement>' [workspace_root]")
        print("  workspace_root defaults to current directory.")
        sys.exit(1)

    user_requirement = sys.argv[1]
    workspace_root = sys.argv[2] if len(sys.argv) > 2 else "."

    state = initial_state(
        user_requirement=user_requirement,
        workspace_root=workspace_root,
    )
    result = invoke(state)

    print("Status:", result.get("status"))
    if result.get("pr_url"):
        print("PR URL:", result["pr_url"])
    if result.get("error_logs"):
        print("Errors:", result["error_logs"])
    if result.get("spec_markdown"):
        print("Spec (first 500 chars):", result["spec_markdown"][:500])


if __name__ == "__main__":
    main()

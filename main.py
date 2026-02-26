#!/usr/bin/env python3
"""
Develop Agent の薄いエントリポイント。
Notion Webhook や Supabase は外側（呼び出し元）で実装し、ここでは graph.invoke のみ行う。
"""

import argparse
import os
import sys
from pathlib import Path

# プロジェクトルートを path に追加（develop_agent の import 前に必要）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from develop_agent import initial_state
from develop_agent.graph import invoke

load_dotenv(".env.local")


IMPROVEMENT_KEYS = (
    "spec_rules_improvement",
    "coder_rules_improvement",
    "review_rules_improvement",
    "fix_rules_improvement",
    "publish_rules_improvement",
)


def write_outputs(result: dict) -> None:
    """output_rules_improvement かつ run_id がある場合、outputs/<run_id>/ に spec と rules/*_improvement.md を書き出す。"""
    if not result.get("output_rules_improvement") or not result.get("run_id"):
        return
    workspace_root = result.get("workspace_root") or "."
    base = Path(workspace_root).resolve()
    output_dir = base / "outputs" / result["run_id"]
    output_dir.mkdir(parents=True, exist_ok=True)

    if result.get("spec_markdown"):
        (output_dir / "spec_markdown.md").write_text(
            result["spec_markdown"], encoding="utf-8"
        )

    rules_dir = output_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for key in IMPROVEMENT_KEYS:
        value = result.get(key)
        if value and isinstance(value, str):
            name = key.replace("_improvement", "_improvement.md")
            (rules_dir / name).write_text(value, encoding="utf-8")

    generated_code = result.get("generated_code") or {}
    if generated_code:
        gen_dir = output_dir / "generated_code"
        gen_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, content in generated_code.items():
            rel_path = rel_path.replace("\\", "/").strip()
            if rel_path.startswith("/"):
                rel_path = rel_path[1:]
            if not rel_path:
                continue
            path = gen_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Develop Agent: 要件から Spec → コード生成 → レビュー → GitHub push まで実行"
    )
    parser.add_argument(
        "user_requirement",
        type=str,
        help="実装してほしい要件（自然言語）",
    )
    parser.add_argument(
        "workspace_root",
        nargs="?",
        default=".",
        help="作業ディレクトリ（デフォルト: カレント）",
    )
    parser.add_argument(
        "--output-rules",
        action="store_true",
        help="改善ルール案を outputs/<run_id>/rules/ に出力する",
    )
    parser.add_argument(
        "--rules-dir",
        type=str,
        default="rules",
        help="ルール .md が入ったディレクトリ（デフォルト: rules）",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="実行 ID（未指定時は自動生成）",
    )
    args = parser.parse_args()

    state = initial_state(
        user_requirement=args.user_requirement,
        workspace_root=args.workspace_root,
        rules_dir=args.rules_dir,
        run_id=args.run_id,
        output_rules_improvement=args.output_rules,
    )
    result = invoke(state)

    write_outputs(result)

    print("Status:", result.get("status"))
    if result.get("run_id"):
        print("Run ID:", result["run_id"])
    if result.get("error_logs"):
        print("Errors:", result["error_logs"])
    if result.get("spec_markdown"):
        print("Spec (first 500 chars):", result["spec_markdown"][:500])


if __name__ == "__main__":
    main()

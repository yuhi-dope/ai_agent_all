"""タスク計画ノード: 自然言語タスク → SaaS 操作計画を LLM で生成。

過去の失敗パターンを学習システムから取得し、プロンプトに注入することで
同じ失敗を繰り返さない計画を立てる。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.state import BPOState
from agent.llm import get_chat_pro
from agent.utils.rule_loader import load_rule

logger = logging.getLogger(__name__)

SYSTEM_TASK_PLANNER = """あなたは企業の AI 社員です。与えられた指示を実行するための SaaS 操作計画を立ててください。

## ルール
1. 利用可能なツールのみを使用すること
2. データ取得（READ）操作を先に、更新（WRITE）操作を後にする
3. 削除操作は含めないこと（手動対応を推奨）
4. 操作は最小限にとどめる（1〜10 ステップ以内）
5. 過去の失敗事例がある場合は、同じ失敗を繰り返さないよう注意する

## 出力形式
以下の2つを出力してください:

### 1. 実行計画（Markdown）
人間が読める形式で手順を説明してください。

### 2. 操作リスト（JSON）
```json
[{"tool_name": "ツール名", "arguments": {"引数1": "値1"}}]
```

必ず上記の形式で、Markdown の実行計画と JSON の操作リストの両方を出力してください。
JSON は ```json ``` ブロック内に記述してください。"""


def task_planner_node(state: BPOState) -> dict[str, Any]:
    """自然言語タスクから SaaS 操作計画を生成するノード。"""
    task_description = state.get("task_description", "")
    saas_name = state.get("saas_name", "")
    genre = state.get("genre", "")
    rules_dir_name = state.get("rules_dir", "rules/saas")
    available_tools = state.get("saas_available_tools") or []

    if not task_description:
        return {
            "status": "failed",
            "error_logs": list(state.get("error_logs") or [])
            + ["タスク計画エラー: task_description が空です"],
        }

    # 1. SaaS 操作ルール読み込み
    rules_dir = Path(rules_dir_name)
    saas_rules = load_rule(rules_dir, "general_rules", "")
    saas_specific_rules = load_rule(rules_dir, f"{saas_name}_rules", "")

    # 2. 過去の失敗パターンを取得（学習システム連携）
    past_failures = _get_past_failure_warnings(saas_name, genre)

    # 3. LLM プロンプト構築
    tools_text = json.dumps(available_tools, ensure_ascii=False, indent=2) if available_tools else "（ツール一覧は未取得）"

    user_message = f"""## 指示
{task_description}

## 対象 SaaS
{saas_name}

## 利用可能なツール
{tools_text}
"""
    if saas_rules:
        user_message += f"\n## SaaS 操作共通ルール\n{saas_rules}\n"
    if saas_specific_rules:
        user_message += f"\n## {saas_name} 固有ルール\n{saas_specific_rules}\n"
    if past_failures:
        user_message += f"\n## 過去の失敗事例（これらを踏まえて計画してください）\n{past_failures}\n"

    # 4. LLM 呼び出し
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_chat_pro()
        response = llm.invoke([
            SystemMessage(content=SYSTEM_TASK_PLANNER),
            HumanMessage(content=user_message),
        ])
        response_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.exception("タスク計画 LLM 呼び出し失敗")
        return {
            "status": "failed",
            "error_logs": list(state.get("error_logs") or [])
            + [f"タスク計画 LLM エラー: {e}"],
        }

    # 5. レスポンスを解析
    plan_markdown, operations = _parse_plan_response(response_text)

    if not operations:
        return {
            "saas_plan_markdown": plan_markdown or response_text,
            "saas_operations": [],
            "status": "failed",
            "error_logs": list(state.get("error_logs") or [])
            + ["タスク計画エラー: 操作リストを生成できませんでした"],
        }

    return {
        "saas_plan_markdown": plan_markdown,
        "saas_operations": operations,
        "status": "awaiting_approval",
    }


def _parse_plan_response(response_text: str) -> tuple[str, list[dict]]:
    """LLM レスポンスから実行計画（Markdown）と操作リスト（JSON）を抽出。"""
    import re

    # JSON ブロックを抽出
    json_match = re.search(r"```json\s*\n(.*?)\n```", response_text, re.DOTALL)
    operations = []
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if isinstance(parsed, list):
                operations = parsed
        except json.JSONDecodeError:
            pass

    # Markdown 部分 = JSON ブロック以外
    plan_markdown = response_text
    if json_match:
        plan_markdown = response_text[:json_match.start()].strip()

    return plan_markdown, operations


def _get_past_failure_warnings(saas_name: str, genre: str) -> str:
    """同じ SaaS の過去失敗をテキストとして取得（学習システム連携）。"""
    if not saas_name:
        return ""
    try:
        from server.saas.task_persist import get_similar_failures

        failures = get_similar_failures(saas_name, genre=genre or None, limit=5)
        if not failures:
            return ""
        lines = []
        for f in failures:
            cat = f.get("failure_category", "unknown")
            reason = f.get("failure_reason", "不明")
            desc = (f.get("task_description") or "")[:100]
            lines.append(f"- [{cat}] {reason}（タスク: {desc}）")
        return "\n".join(lines)
    except Exception:
        return ""

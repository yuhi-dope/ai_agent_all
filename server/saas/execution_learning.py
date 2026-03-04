"""実行ナレッジ学習 + ベストプラクティスルール自動生成.

蓄積された実行ナレッジ（成功パターン）から、操作のベストプラクティスルールを自動生成する。
既存の rule_changes テーブル + 承認フローを再利用。

フロー:
  1. execution_knowledge の成功パターンを task_type ごとに集計
  2. 同じ task_type のパターンが EXECUTION_RULE_THRESHOLD 回以上蓄積
  3. LLM でベストプラクティスルールを自動生成（「こうすべき」形式）
  4. rule_changes テーブルに INSERT（status='pending'）
  5. 管理者が承認 → ルールファイルに反映
  6. 次回計画時にルールが読み込まれる

企業固有データは含めず、匿名化された操作パターンのみをルール化する。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXECUTION_RULE_THRESHOLD = 1


def _get_client():
    """Supabase クライアントを返す。"""
    from server._supabase import get_client
    return get_client()


def check_and_generate_execution_rules(saas_name: Optional[str] = None) -> list[str]:
    """蓄積された実行ナレッジからベストプラクティスルール候補を自動生成。

    Returns:
        生成された rule_change の ID リスト。
    """
    from server.saas.task_persist import get_execution_patterns

    patterns = get_execution_patterns(
        saas_name=saas_name,
        success_only=True,
        min_count=EXECUTION_RULE_THRESHOLD,
    )
    if not patterns:
        return []

    generated_ids: list[str] = []
    for pattern in patterns:
        if _execution_rule_candidate_exists(pattern):
            continue

        rule_text = _generate_execution_rule(pattern)
        if not rule_text:
            continue

        change_id = _save_execution_rule_candidate(pattern, rule_text)
        if change_id:
            generated_ids.append(change_id)
            logger.info(
                "実行ルール候補生成: saas=%s, genre=%s, task_type=%s, count=%d",
                pattern.get("saas_name"),
                pattern.get("genre"),
                pattern.get("task_type"),
                pattern.get("count", 0),
            )

    return generated_ids


def _execution_rule_candidate_exists(pattern: dict) -> bool:
    """同じ実行パターンのルール候補が既に rule_changes に存在するかチェック。"""
    client = _get_client()
    if not client:
        return False
    try:
        sn = pattern.get("saas_name", "general")
        task_type = pattern.get("task_type", "unknown")
        run_id_prefix = f"auto_execution_{sn}_{task_type}"

        r = (
            client.table("rule_changes")
            .select("id")
            .like("run_id", f"{run_id_prefix}%")
            .in_("status", ["pending", "approved"])
            .limit(1)
            .execute()
        )
        return bool(r.data)
    except Exception:
        return False


def _generate_execution_rule(pattern: dict) -> str:
    """LLM で実行パターンからベストプラクティスルールを生成。"""
    try:
        from agent.llm import get_chat_flash
        from langchain_core.messages import HumanMessage, SystemMessage

        examples = pattern.get("examples", [])
        examples_text = ""
        for i, ex in enumerate(examples[:3], 1):
            ops = ex.get("operation_sequence", [])
            if isinstance(ops, str):
                ops = json.loads(ops)
            ops_summary = " → ".join(
                f"{op.get('tool_name', '?')}({'OK' if op.get('success') else 'NG'})"
                for op in ops
            )
            examples_text += (
                f"\n例{i}（成功: {ex.get('success_count', 0)}件）:\n"
                f"  タスク概要: {ex.get('task_description', '')[:100]}\n"
                f"  操作フロー: {ops_summary}\n"
            )

        llm = get_chat_flash()
        prompt = f"""以下の SaaS 操作の成功パターンに基づいて、今後の操作計画で活用すべきベストプラクティスルールを日本語で生成してください。

## 成功パターン
- SaaS: {pattern.get("saas_name", "不明")}
- ジャンル: {pattern.get("genre") or "全般"}
- タスク種別: {pattern.get("task_type", "不明")}
- 成功事例数: {pattern.get("count", 0)}件

## 成功事例
{examples_text}

## ルール生成の方針
- 「避けるべき」ではなく「こうすべき」「この手順が効果的」というポジティブな表現にする
- 成功した操作の順序・組み合わせパターンを抽出する
- パラメータの設定で効果的だった点を抽出する
- 複数事例で共通するパターンを優先する

## 出力形式
Markdown の1セクションとして出力してください:
- タイトル行（## で始まる）には「ベストプラクティス」というキーワードを含めること
- 2〜4 個の箇条書きで具体的な推奨事項を記述
- 「〇〇の場合は、△△の手順で操作すると成功率が高い」という形式
- 企業名や具体的なデータは含めないこと（匿名化パターンのみ）"""

        response = llm.invoke([
            SystemMessage(
                content="あなたは SaaS 操作のベストプラクティスルール生成アシスタントです。"
                "成功パターンから、今後の操作で再現すべき効果的な手順やアプローチを抽出してルール化します。"
            ),
            HumanMessage(content=prompt),
        ])
        return response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("実行ルール生成 LLM 呼び出し失敗")
        return ""


def _save_execution_rule_candidate(pattern: dict, rule_text: str) -> str | None:
    """実行ルール候補を rule_changes テーブルに保存。"""
    from agent.utils.rule_loader import GENRE_TO_JAPANESE

    client = _get_client()
    if not client:
        return None
    try:
        sn = pattern.get("saas_name", "general")
        genre = (pattern.get("genre") or "").strip()
        task_type = pattern.get("task_type", "unknown")
        ja_genre = GENRE_TO_JAPANESE.get(genre, "") if genre else ""

        if ja_genre:
            rule_name = f"saas_learned_{sn}_{ja_genre}"
        else:
            rule_name = f"saas_{sn}"

        row = {
            "run_id": f"auto_execution_{sn}_{task_type}",
            "rule_name": rule_name,
            "added_block": rule_text,
            "genre": genre or None,
            "status": "pending",
        }
        r = client.table("rule_changes").insert(row).execute()
        if r.data:
            return r.data[0].get("id", "")
        return None
    except Exception:
        return None

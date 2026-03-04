"""修正駆動学習 + 意図解釈ルール自動生成.

蓄積されたタスク修正パターンから、意図解釈ルール候補を自動生成する。
既存の rule_changes テーブル + 承認フローを再利用。

フロー:
  1. task_corrections の成功した修正パターンを集計
  2. 同じパターンが CORRECTION_RULE_THRESHOLD 回以上蓄積
  3. LLM で意図解釈ルールを自動生成
  4. rule_changes テーブルに INSERT（status='pending'）
  5. 管理者が承認 → ルールファイルに反映
  6. 次回計画時にルールが読み込まれる

企業固有データは含めず、匿名化された解釈パターンのみをルール化する。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 1回でもルール候補を生成する
CORRECTION_RULE_THRESHOLD = 1


def _get_client():
    """Supabase クライアントを返す。"""
    from server._supabase import get_client
    return get_client()


def check_and_generate_correction_rules(saas_name: Optional[str] = None) -> list[str]:
    """蓄積された修正パターンから意図解釈ルール候補を自動生成。

    Returns:
        生成された rule_change の ID リスト。
    """
    from server.saas.task_persist import get_correction_patterns

    patterns = get_correction_patterns(
        saas_name=saas_name,
        min_count=CORRECTION_RULE_THRESHOLD,
    )
    if not patterns:
        return []

    generated_ids: list[str] = []
    for pattern in patterns:
        if _correction_rule_candidate_exists(pattern):
            continue

        rule_text = _generate_correction_rule(pattern)
        if not rule_text:
            continue

        change_id = _save_correction_rule_candidate(pattern, rule_text)
        if change_id:
            generated_ids.append(change_id)
            logger.info(
                "修正ルール候補生成: saas=%s, pattern=%s, count=%d",
                pattern.get("saas_name"),
                pattern.get("pattern_summary"),
                pattern.get("count", 0),
            )

    return generated_ids


def _correction_rule_candidate_exists(pattern: dict) -> bool:
    """同じ修正パターンのルール候補が既に rule_changes に存在するかチェック。"""
    client = _get_client()
    if not client:
        return False
    try:
        sn = pattern.get("saas_name", "general")
        pattern_summary = pattern.get("pattern_summary", "")
        run_id_prefix = f"auto_correction_{sn}_{pattern_summary}"

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


def _generate_correction_rule(pattern: dict) -> str:
    """LLM で修正パターンから意図解釈ルールを生成。"""
    try:
        from agent.llm import get_chat_flash
        from langchain_core.messages import HumanMessage, SystemMessage

        examples = pattern.get("examples", [])
        examples_text = ""
        for i, ex in enumerate(examples[:3], 1):
            examples_text += (
                f"\n例{i}:\n"
                f"  修正前: {ex.get('original', '')}\n"
                f"  修正後: {ex.get('modified', '')}\n"
            )

        llm = get_chat_flash()
        prompt = f"""以下の SaaS タスク記述の修正パターンに基づいて、今後のタスク計画時に曖昧な指示を正しく解釈するためのルールを日本語で生成してください。

## 修正パターン
- SaaS: {pattern.get("saas_name", "不明")}
- パターン種別: {pattern.get("pattern_summary", "不明")}
- 発生回数: {pattern.get("count", 0)}回

## 修正例（ユーザーが元の指示をどう具体化したか）
{examples_text}

## 出力形式
Markdown の1セクションとして出力してください:
- タイトル行（## で始まる）には「意図解釈」というキーワードを含めること
- 2〜3 個の箇条書きで、計画者がどう解釈すべきかのガイダンスを記述
- 「〇〇という指示の場合、△△を確認・推測すべき」という形式
- 企業名や具体的なデータは含めないこと（匿名化パターンのみ）"""

        response = llm.invoke([
            SystemMessage(content="あなたは SaaS 操作の意図解釈ルール生成アシスタントです。ユーザーの曖昧な指示パターンから、計画者が正しく解釈するためのルールを作成します。"),
            HumanMessage(content=prompt),
        ])
        return response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("修正ルール生成 LLM 呼び出し失敗")
        return ""


def _save_correction_rule_candidate(pattern: dict, rule_text: str) -> str | None:
    """修正ルール候補を rule_changes テーブルに保存。"""
    from agent.utils.rule_loader import GENRE_TO_JAPANESE

    client = _get_client()
    if not client:
        return None
    try:
        sn = pattern.get("saas_name", "general")
        genre = (pattern.get("genre") or "").strip()
        ja_genre = GENRE_TO_JAPANESE.get(genre, "") if genre else ""

        if ja_genre:
            rule_name = f"saas_learned_{sn}_{ja_genre}"
        else:
            rule_name = f"saas_{sn}"

        row = {
            "run_id": f"auto_correction_{sn}_{pattern.get('pattern_summary', 'unknown')}",
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

"""失敗学習 + ルール自動生成.

蓄積された SaaS 操作の失敗パターンから、操作ルール候補を自動生成する。
既存の rule_changes テーブル + 承認フローを再利用。

フロー:
  1. saas_tasks の failure_reason/category を集計
  2. 同じパターンが RULE_GENERATION_THRESHOLD 回以上蓄積
  3. LLM でルールテキストを自動生成
  4. rule_changes テーブルに INSERT（status='pending'）
  5. 管理者が承認 → rules/saas/*.md に反映
  6. 全企業の AI 社員がこのルールを読み込む

企業固有データは含めず、匿名化された操作パターンのみをルール化する。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

RULE_GENERATION_THRESHOLD = 2


def _get_client():
    """Supabase クライアントを返す。"""
    from server._supabase import get_client
    return get_client()


def check_and_generate_rules(saas_name: Optional[str] = None) -> list[str]:
    """蓄積された失敗パターンからルール候補を自動生成。

    Returns:
        生成された rule_change の ID リスト。
    """
    from server.saas.task_persist import get_failure_patterns

    patterns = get_failure_patterns(
        saas_name=saas_name,
        min_count=RULE_GENERATION_THRESHOLD,
    )
    if not patterns:
        return []

    generated_ids: list[str] = []
    for pattern in patterns:
        if _rule_candidate_exists(pattern):
            continue

        rule_text = _generate_rule_from_pattern(pattern)
        if not rule_text:
            continue

        change_id = _save_rule_candidate(pattern, rule_text)
        if change_id:
            generated_ids.append(change_id)
            logger.info(
                "ルール候補生成: saas=%s, category=%s, count=%d",
                pattern.get("saas_name"),
                pattern.get("failure_category"),
                pattern.get("count", 0),
            )

    return generated_ids


def _rule_candidate_exists(pattern: dict) -> bool:
    """同じパターンのルール候補が既に rule_changes に存在するかチェック。"""
    client = _get_client()
    if not client:
        return False
    try:
        sn = pattern.get("saas_name", "general")
        rule_name = f"saas_{sn}"
        cat = pattern.get("failure_category", "")
        reason = pattern.get("failure_reason", "")

        r = (
            client.table("rule_changes")
            .select("id")
            .eq("rule_name", rule_name)
            .limit(50)
            .execute()
        )
        if not r.data:
            return False
        # added_block にカテゴリ+理由のキーワードが含まれているかチェック
        for row in r.data:
            # 簡易的な重複チェック: rule_name が一致し pending/approved のものがあれば既存とみなす
            pass
        # カテゴリ+理由ベースの重複チェック
        r2 = (
            client.table("rule_changes")
            .select("id, added_block")
            .eq("rule_name", rule_name)
            .in_("status", ["pending", "approved"])
            .limit(50)
            .execute()
        )
        for row in (r2.data or []):
            block = row.get("added_block", "")
            if cat in block and reason[:30] in block:
                return True
        return False
    except Exception:
        return False


def _generate_rule_from_pattern(pattern: dict) -> str:
    """LLM で失敗パターンから日本語ルールを生成。

    企業固有データは含めず、操作パターンのみ抽出する。
    """
    try:
        from agent.llm import get_chat_flash
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_chat_flash()
        prompt = f"""以下の SaaS 操作の失敗パターンに基づいて、今後の操作で避けるべきルールを日本語で生成してください。

## 失敗パターン
- SaaS: {pattern.get("saas_name", "不明")}
- 失敗カテゴリ: {pattern.get("failure_category", "不明")}
- 失敗理由: {pattern.get("failure_reason", "不明")}
- 発生回数: {pattern.get("count", 0)}回

## 出力形式
Markdown の1セクションとして出力してください:
- タイトル行（## で始まる）
- 2〜3 個の箇条書きで具体的なガイダンス
- 企業名や具体的なデータは含めないこと（匿名化パターンのみ）"""

        response = llm.invoke([
            SystemMessage(content="あなたは SaaS 操作ルールの生成アシスタントです。失敗パターンから再発防止ルールを作成します。"),
            HumanMessage(content=prompt),
        ])
        return response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception("ルール生成 LLM 呼び出し失敗")
        return ""


def _save_rule_candidate(pattern: dict, rule_text: str) -> str | None:
    """ルール候補を rule_changes テーブルに保存。"""
    client = _get_client()
    if not client:
        return None
    try:
        sn = pattern.get("saas_name", "general")
        row = {
            "run_id": f"auto_learning_{sn}_{pattern.get('failure_category', 'unknown')}",
            "rule_name": f"saas_{sn}",
            "added_block": rule_text,
            "genre": pattern.get("genre") or None,
            "status": "pending",
        }
        r = client.table("rule_changes").insert(row).execute()
        if r.data:
            return r.data[0].get("id", "")
        return None
    except Exception:
        return None

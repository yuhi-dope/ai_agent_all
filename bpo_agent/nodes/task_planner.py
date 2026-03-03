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
from agent.utils.rule_loader import load_bpo_rules

logger = logging.getLogger(__name__)

SYSTEM_TASK_PLANNER = """あなたは企業の AI 社員です。与えられた指示を実行するための SaaS 操作計画を立ててください。

## ルール
1. 利用可能なツールのみを使用すること
2. 削除操作は含めないこと（手動対応を推奨）
3. 操作は最小限にとどめる（1〜10 ステップ以内）
4. 過去の失敗事例がある場合は、同じ失敗を繰り返さないよう注意する

## 重要: コンテキストに既にあるデータは再取得しない
- コンテキストにフィールド定義、レイアウト、ビュー設定が含まれている場合、それらのデータは取得済みです
- 取得済みデータを再度 GET する操作は計画に含めないでください
- コンテキストの情報を直接使って、更新（WRITE）操作を計画してください

## 重要: 1回の計画で完結させる
- 「まず情報取得して、次のステップで改善する」のような段階的な計画は禁止
- 必要な操作（READ も WRITE も）をすべて1回の計画に含めてください
- 更新系操作の後にデプロイ（deploy）が必要な場合は、最後にデプロイ操作も含めること

## 出力形式（厳守）
以下の2つを**必ず両方**出力してください。操作リスト（JSON）がない回答は無効です。

### 1. 実行計画（Markdown）
人間が読める形式で手順を説明してください。

### 2. 操作リスト（JSON）
利用可能なツールの tool_name と arguments を使って、具体的な操作を JSON 配列で出力してください。

出力例（kintone のレイアウト更新の場合）:
```json
[
  {"tool_name": "kintone_update_layout", "arguments": {"app_id": "685", "layout": [...]}},
  {"tool_name": "kintone_deploy_app", "arguments": {"app_id": "685"}}
]
```

出力例（レコード追加の場合）:
```json
[
  {"tool_name": "kintone_add_record", "arguments": {"app_id": "100", "record": {"フィールドコード": {"value": "値"}}}}
]
```

**操作リスト（JSON）は必須です。必ず1つ以上の操作を含めてください。**
**```json``` ブロック内に JSON 配列を記述してください。Markdown の説明だけでは不十分です。**

### 3. 計画の確信度と注意事項（JSON）
計画の確信度と注意事項を以下の JSON 形式で ```json``` ブロック内に出力してください。

```json
{"confidence": 0.85, "warnings": []}
```

confidence の基準:
- 0.9〜1.0: 指示が明確、コンテキスト十分、過去失敗なし
- 0.7〜0.8: 指示にやや曖昧な部分あり、または一部コンテキスト不足
- 0.5〜0.6: 指示が曖昧、コンテキスト不足、推測が多い
- 0.5未満: 指示が理解困難、必要情報が大幅に不足

warnings に含める例:
- 指示が曖昧な場合: 「『見やすく』という指示が曖昧です。具体的な改善箇所を指定すると精度が上がります」
- コンテキスト不足: 「現在のレイアウト情報が未取得のため、既存配置との衝突リスクがあります」
- 推測を含む場合: 「フィールドタイプを推測しています。実際の定義と異なる可能性があります」
- 大量操作: 「10件以上のレコードを更新します。分割実行を検討してください」

warnings が無い場合は空配列 [] としてください。"""


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

    # 1. SaaS 操作ルール読み込み（4層合成: general + platform + genre + learned）
    rules_dir = Path(rules_dir_name)
    combined_rules = load_bpo_rules(rules_dir, saas_name, genre)

    # 2. 過去の失敗パターンを取得（学習システム連携）
    past_failures = _get_past_failure_warnings(saas_name, genre)

    # 3. LLM プロンプト構築
    tools_text = json.dumps(available_tools, ensure_ascii=False, indent=2) if available_tools else "（ツール一覧は未取得）"

    # SaaS コンテキスト（アプリ一覧等の実データ）
    saas_context = state.get("saas_context", "")

    user_message = f"""## 指示
{task_description}

## 対象 SaaS
{saas_name}

## 利用可能なツール
{tools_text}
"""
    if saas_context:
        user_message += f"\n{saas_context}\n"
    if combined_rules:
        user_message += f"\n## SaaS 操作ルール\n{combined_rules}\n"
    if past_failures:
        user_message += f"\n## 過去の失敗事例（これらを踏まえて計画してください）\n{past_failures}\n"

    # コンテキスト内容をログ出力（デバッグ用）
    logger.info(
        "タスク計画 コンテキスト: saas=%s, context_len=%d, tools=%d, context_preview=%s",
        saas_name, len(saas_context), len(available_tools), saas_context[:300] if saas_context else "(empty)",
    )

    # 4. LLM 呼び出し（失敗時は最大2回リトライ）
    MAX_ATTEMPTS = 3
    response_text = ""
    plan_markdown = ""
    operations = []

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            llm = get_chat_pro(max_output_tokens=16384)
            messages = [
                SystemMessage(content=SYSTEM_TASK_PLANNER),
                HumanMessage(content=user_message),
            ]
            # リトライ時: 前回のレスポンスを含めて修正を指示
            if attempt > 1 and response_text:
                messages.append(AIMessage(content=response_text))
                if "[システム注記:" in response_text:
                    # READ のみ検出 → WRITE 操作を強制
                    messages.append(HumanMessage(
                        content="あなたの前回の計画は情報取得（GET/READ）操作のみでした。\n"
                        "コンテキストにはフィールド定義・レイアウト・ビュー情報が既に含まれています。\n"
                        "**再取得は不要です。** コンテキストの情報をもとに、更新系の操作を計画してください。\n\n"
                        "例えば UI/UX 改善なら:\n"
                        "```json\n"
                        "[\n"
                        '  {"tool_name": "kintone_update_layout", "arguments": {"app_id": "685", "layout": [...]}},\n'
                        '  {"tool_name": "kintone_update_views", "arguments": {"app_id": "685", "views": {...}}},\n'
                        '  {"tool_name": "kintone_deploy_app", "arguments": {"app_id": "685"}}\n'
                        "]\n"
                        "```\n\n"
                        "```json``` ブロック内に WRITE 操作を含む JSON 配列を出力してください。"
                    ))
                else:
                    # JSON なし → JSON 出力を強制
                    messages.append(HumanMessage(
                        content="あなたの出力には ```json [...] ``` 形式の操作リストが含まれていません。"
                        "操作リスト（JSON配列）は必須です。\n\n"
                        "利用可能なツール一覧を参照して、具体的な tool_name と arguments を使った JSON 配列を "
                        "```json``` ブロック内に出力してください。\n"
                        "例: ```json\n"
                        '[{"tool_name": "kintone_update_layout", "arguments": {"app_id": "685", "layout": [...]}}]\n'
                        "```\n\n"
                        "Markdown の実行計画は不要です。JSON 操作リストのみ出力してください。"
                    ))

            response = llm.invoke(messages)
            response_text = response.content if hasattr(response, "content") else str(response)
            logger.info("タスク計画 LLM レスポンス (attempt=%d, %d文字): %s", attempt, len(response_text), response_text[:500])
        except Exception as e:
            logger.exception("タスク計画 LLM 呼び出し失敗 (attempt=%d)", attempt)
            if attempt >= MAX_ATTEMPTS:
                return {
                    "status": "failed",
                    "error_logs": list(state.get("error_logs") or [])
                    + [f"タスク計画 LLM エラー: {e}"],
                }
            continue

        # 5. レスポンスを解析
        plan_markdown, operations, confidence, warnings = _parse_plan_response(response_text)
        if not operations:
            logger.warning("操作リスト抽出失敗 (attempt=%d/%d)", attempt, MAX_ATTEMPTS)
            continue

        # 6. READ のみの計画を検出 → コンテキストに既データがあればリトライ
        read_only = all(
            op.get("tool_name", "").startswith(f"{saas_name}_get_")
            for op in operations
        )
        if read_only and saas_context and attempt < MAX_ATTEMPTS:
            logger.warning(
                "READ のみの計画を検出 (attempt=%d/%d)。コンテキストに既データあり → リトライ",
                attempt, MAX_ATTEMPTS,
            )
            # リトライ用にレスポンスを保持し、次のループで強い指示を出す
            # response_text にセットしておくことで、次の attempt で AIMessage として送られる
            response_text = response_text + (
                "\n\n[システム注記: 上記の計画は情報取得（READ）操作のみです。"
                "コンテキストに既にフィールド定義・レイアウト・ビュー情報が含まれているため、"
                "それらの再取得は不要です。更新（WRITE）操作を含む計画を生成してください。]"
            )
            operations = []
            continue

        break

    if not operations:
        return {
            "saas_plan_markdown": plan_markdown or response_text,
            "saas_operations": [],
            "plan_confidence": confidence,
            "plan_warnings": warnings,
            "status": "failed",
            "error_logs": list(state.get("error_logs") or [])
            + ["タスク計画エラー: 操作リストを生成できませんでした"],
        }

    return {
        "saas_plan_markdown": plan_markdown,
        "saas_operations": operations,
        "plan_confidence": confidence,
        "plan_warnings": warnings,
        "status": "awaiting_approval",
    }


def _parse_plan_response(response_text: str) -> tuple[str, list[dict], float, list[str]]:
    """LLM レスポンスから実行計画（Markdown）、操作リスト（JSON）、確信度、警告を抽出。"""
    import re

    operations: list[dict] = []
    json_match = None
    confidence = 0.0
    warnings: list[str] = []

    # パターン1: ```json ... ``` ブロック（複数ある場合は最大の配列を採用）
    json_blocks = list(re.finditer(r"```json\s*(.*?)\s*```", response_text, re.DOTALL))
    if json_blocks:
        # tool_name を含む JSON ブロックを優先
        for m in json_blocks:
            if "tool_name" in m.group(1):
                json_match = m
                break
        if not json_match:
            # tool_name がないブロックから confidence を含まないものを選ぶ
            for m in json_blocks:
                if "confidence" not in m.group(1):
                    json_match = m
                    break

    # パターン2: ``` ... ``` ブロック内に JSON 配列がある場合
    if not json_match:
        json_match = re.search(r"```\s*(\[.*?\])\s*```", response_text, re.DOTALL)

    # パターン3: コードブロックなしで直接 JSON 配列が書かれている場合
    if not json_match:
        json_match = re.search(r"(\[\s*\{.*?\"tool_name\".*?\}\s*\])", response_text, re.DOTALL)

    # パターン4: 単一オブジェクト（配列でない）で tool_name がある場合
    if not json_match:
        json_match = re.search(r"(\{[^{}]*\"tool_name\"[^{}]*\})", response_text, re.DOTALL)

    if json_match:
        json_text = json_match.group(1).strip()
        # JSON 内のコメント行を除去（LLM が // コメントを入れることがある）
        json_text = re.sub(r'//[^\n]*', '', json_text)
        # 末尾カンマを除去（LLM が trailing comma を入れることがある）
        json_text = re.sub(r',\s*([}\]])', r'\1', json_text)
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, list):
                operations = [op for op in parsed if isinstance(op, dict) and "tool_name" in op]
            elif isinstance(parsed, dict) and "tool_name" in parsed:
                operations = [parsed]
        except json.JSONDecodeError as e:
            logger.warning("操作リスト JSON パース失敗: %s\nJSON text: %s", e, json_text[:500])

    if not operations:
        logger.warning("操作リストが抽出できませんでした。LLM レスポンス: %s", response_text[:1000])

    # 確信度・警告の抽出: confidence を含む JSON ブロックを探す
    for m in json_blocks:
        block_text = m.group(1).strip()
        if "confidence" in block_text and "tool_name" not in block_text:
            cleaned = re.sub(r'//[^\n]*', '', block_text)
            cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
            try:
                meta = json.loads(cleaned)
                if isinstance(meta, dict) and "confidence" in meta:
                    confidence = float(meta.get("confidence", 0.0))
                    raw_warnings = meta.get("warnings", [])
                    warnings = raw_warnings if isinstance(raw_warnings, list) else []
                    break
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    # フォールバック: ブロック外に confidence がある場合
    if confidence == 0.0:
        conf_match = re.search(r'"confidence"\s*:\s*([\d.]+)', response_text)
        if conf_match:
            try:
                confidence = float(conf_match.group(1))
            except ValueError:
                pass

    # Markdown 部分 = JSON ブロック以外
    plan_markdown = response_text
    if json_match:
        plan_markdown = response_text[:json_match.start()].strip()
        # 末尾の「操作リスト」見出しを除去（JSON は別途 UI に表示するため）
        plan_markdown = re.sub(
            r'\n*#{1,4}\s*\d*\.?\s*操作リスト.*$', '', plan_markdown, flags=re.DOTALL
        ).strip()

    return plan_markdown, operations, confidence, warnings


_CATEGORY_LABELS = {
    "field_error": ("フィールド関連エラー", "フィールドコードの重複・存在確認、選択肢の完全一致に注意してください"),
    "query_error": ("クエリ構文エラー", "DROP_DOWN/CHECK_BOX 等には in 演算子を使い、= は使わないでください"),
    "auth_error": ("認証エラー", "接続の有効性を確認してください"),
    "validation_error": ("バリデーションエラー", "必須パラメータや値の形式を確認してください"),
    "exec_error": ("実行エラー", "操作パラメータの正確性を確認してください"),
    "planning_error": ("計画エラー", "操作リストの出力形式を確認してください"),
    "record_error": ("レコード操作エラー", "record_id の事前取得や値の形式を確認してください"),
}


def _get_past_failure_warnings(saas_name: str, genre: str) -> str:
    """同じ SaaS の過去失敗をカテゴリ別にグルーピングしてテキスト化。"""
    if not saas_name:
        return ""
    try:
        from server.saas.task_persist import get_similar_failures

        failures = get_similar_failures(saas_name, genre=genre or None, limit=10)
        if not failures:
            return ""

        # カテゴリ別にグルーピング
        from collections import defaultdict
        grouped: dict[str, list[str]] = defaultdict(list)
        for f in failures:
            cat = f.get("failure_category", "unknown")
            reason = f.get("failure_reason", "不明")
            # 同じ理由の重複を避ける
            if reason not in grouped[cat]:
                grouped[cat].append(reason)

        lines = []
        for cat, reasons in grouped.items():
            label, advice = _CATEGORY_LABELS.get(cat, (cat, ""))
            lines.append(f"### {label}")
            for reason in reasons[:3]:  # カテゴリごとに最大3件
                lines.append(f"- {reason}")
            if advice:
                lines.append(f"→ 対策: {advice}")
            lines.append("")

        return "\n".join(lines)
    except Exception:
        return ""

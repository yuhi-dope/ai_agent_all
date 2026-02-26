"""
Genre Classifier Node: 要件テキストからジャンルを AI 自動判定する LangGraph ノード。

- ユーザー指定 genre がない場合 → AI が判定して設定する
- ユーザー指定 genre がある場合 → AI が評価し、confidence >= 0.85 かつ別ジャンルと判断した場合のみ上書き
- 分類は rules/genre_rules.md（共通ルール + 個社追記）に従う
- Gemini Flash を使用（コスト最小化）
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agent.state import AgentState
from agent.llm import get_chat_flash
from agent.utils.rule_loader import load_rule
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

GENRE_CLASSIFIER_SYSTEM = """あなたは業務要件のジャンル分類専門家です。
渡された「分類ルール」と「要件テキスト」を読み、最も適切なジャンルを判定してください。

出力は必ず以下の JSON 形式のみとし、余計な説明や前置きは一切書かないでください:
{
  "genre_id": "sfa|crm|accounting|legal|admin|it|marketing|design|ma|no2",
  "genre_subcategory": "（ジャンル内の細分類。例: 商談管理、顧客分析、請求書発行 等）",
  "confidence": 0.0～1.0,
  "reason": "（判定理由を1～2文で）"
}"""

OVERRIDE_THRESHOLD = 0.85


def _extract_json(text: str) -> dict | None:
    """LLM 出力から JSON オブジェクトを抽出する。マークダウンコードブロック対応。"""
    text = text.strip()
    # ```json ... ``` または ``` ... ``` のコードブロックを除去
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 部分のみ抽出を試みる
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def genre_classifier_node(state: AgentState) -> dict:
    """
    user_requirement を読み、genre / genre_subcategory / genre_override_reason を返す。
    genre_rules.md がなければ既存の genre をそのまま維持してスキップする。
    """
    req = (state.get("user_requirement") or "").strip()
    if not req:
        return {}

    workspace_root = state.get("workspace_root") or "."
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = Path(workspace_root) / rules_dir_name

    genre_rules = load_rule(rules_dir, "genre_rules", "")
    if not genre_rules.strip():
        # genre_rules.md が存在しない場合はスキップ（既存 genre を維持）
        logger.debug("genre_rules.md not found, skipping genre classification")
        return {}

    user_genre = (state.get("genre") or "").strip()

    user_content = (
        f"## 分類ルール\n\n{genre_rules}\n\n"
        f"## 要件テキスト\n\n{req[:2000]}\n\n"
        f"## ユーザー指定ジャンル（空の場合もある）\n\n{user_genre or '（指定なし）'}"
    )

    try:
        llm = get_chat_flash()
        response = llm.invoke([
            SystemMessage(content=GENRE_CLASSIFIER_SYSTEM),
            HumanMessage(content=user_content),
        ])
        raw = response.content if hasattr(response, "content") else str(response)
        parsed = _extract_json(raw)
    except Exception as e:
        logger.warning("genre_classifier LLM call failed: %s", e)
        return {}

    if not parsed or "genre_id" not in parsed:
        logger.warning("genre_classifier: could not parse LLM output: %s", raw[:200])
        return {}

    detected_genre = (parsed.get("genre_id") or "").strip()
    subcategory = (parsed.get("genre_subcategory") or "").strip()
    confidence = float(parsed.get("confidence") or 0.0)
    reason = (parsed.get("reason") or "").strip()

    out: dict = {}

    if user_genre:
        # ユーザー指定あり: 高確信度かつ別ジャンルの場合のみ上書き
        if detected_genre and detected_genre != user_genre and confidence >= OVERRIDE_THRESHOLD:
            override_reason = (
                f"ユーザー指定「{user_genre}」→ AI 判定「{detected_genre}」"
                f"（confidence={confidence:.2f}）: {reason}"
            )
            logger.info("genre override: %s", override_reason)
            out["genre"] = detected_genre
            out["genre_override_reason"] = override_reason
        else:
            # ユーザー指定を尊重（サブカテゴリのみ補完）
            pass
    else:
        # ユーザー指定なし: AI 判定を使用
        if detected_genre:
            out["genre"] = detected_genre

    if subcategory:
        out["genre_subcategory"] = subcategory

    return out

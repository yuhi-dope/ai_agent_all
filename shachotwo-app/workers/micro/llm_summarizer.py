"""llm_summarizer マイクロエージェント。長文ドキュメントを要約する。"""
import json
import time
import logging
import re
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError
from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

_AGENT_NAME = "llm_summarizer"
_SHORT_TEXT_THRESHOLD = 500  # これ以下の文字数はLLM不使用


def _summarizer_build_system_prompt(style: str, language: str) -> str:
    if language == "ja":
        base = "あなたは文書要約の専門家です。与えられた文書を正確かつ簡潔に要約してください。"
    else:
        base = "You are a document summarization expert. Summarize the given document accurately and concisely."

    if style == "structured":
        return (
            base + "\n必ずJSON形式のみで返してください（説明文不要）。"
            "\nJSONのキー: title, summary, key_points（配列）, risks（配列）, action_items（配列）"
        )
    return base


def _summarizer_build_user_prompt(
    text: str,
    style: str,
    max_length: int,
    focus: str | None,
    language: str,
) -> str:
    focus_clause = f"特に「{focus}」に注目して、" if focus else ""

    if style == "bullet":
        instruction = f"{focus_clause}以下の文書を箇条書き（最大10項目）で要約してください。"
    elif style == "structured":
        instruction = (
            f"{focus_clause}以下の文書をJSON形式で要約してください。"
            "キー: title, summary, key_points[], risks[], action_items[]"
        )
    else:  # paragraph
        instruction = f"{focus_clause}以下の文書を{max_length}文字以内で要約してください。"

    return f"{instruction}\n\n---\n{text[:8000]}\n---"


def _parse_structured_response(raw: str) -> dict[str, Any] | None:
    """structured styleのLLMレスポンスをJSONパースする。失敗時はNoneを返す。"""
    # コードブロック除去
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()
    json_match = re.search(r"\{[\s\S]*\}", cleaned)
    if not json_match:
        return None
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return None


async def run_llm_summarizer(inp: MicroAgentInput) -> MicroAgentOutput:
    """
    長文ドキュメントを要約するマイクロエージェント。

    payload:
        text (str): 要約対象の長文テキスト
        max_length (int, optional): 要約の最大文字数（default=500）
        style (str, optional): "bullet" | "paragraph" | "structured"（default="bullet"）
        focus (str, optional): 特に注目すべきポイント
        language (str, optional): 出力言語（default="ja"）

    result:
        summary (str): 要約テキスト
        original_length (int): 元テキストの文字数
        summary_length (int): 要約テキストの文字数
        compression_ratio (float): 要約率（summary_length / original_length）
        style (str): 使用したスタイル
        key_points (list[str]): キーポイント一覧（structured style時）
        risks (list[str]): リスク一覧（structured style時）
        action_items (list[str]): アクションアイテム一覧（structured style時）
    """
    start_ms = int(time.time() * 1000)

    try:
        text: str = inp.payload.get("text", "")
        max_length: int = inp.payload.get("max_length", 500)
        style: str = inp.payload.get("style", "bullet")
        focus: str | None = inp.payload.get("focus")
        language: str = inp.payload.get("language", "ja")

        # 空テキストチェック
        if not text or not text.strip():
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=_AGENT_NAME,
                success=False,
                result={"error": "text が空です"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        original_length = len(text)

        # 短文はLLM不使用でそのまま返す
        if original_length <= _SHORT_TEXT_THRESHOLD:
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=_AGENT_NAME,
                success=True,
                result={
                    "summary": text,
                    "original_length": original_length,
                    "summary_length": original_length,
                    "compression_ratio": 1.0,
                    "style": style,
                    "key_points": [],
                    "risks": [],
                    "action_items": [],
                },
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        # LLM呼び出し
        llm = get_llm_client()
        system_prompt = _summarizer_build_system_prompt(style, language)
        user_prompt = _summarizer_build_user_prompt(text, style, max_length, focus, language)

        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.FAST,
            task_type=_AGENT_NAME,
            company_id=inp.company_id,
            max_tokens=1024,
            temperature=0.3,
        ))

        raw_content = response.content.strip()

        # structured styleのパース
        key_points: list[str] = []
        risks: list[str] = []
        action_items: list[str] = []
        actual_style = style

        if style == "structured":
            parsed = _parse_structured_response(raw_content)
            if parsed is not None:
                summary = parsed.get("summary", raw_content)
                key_points = parsed.get("key_points", [])
                risks = parsed.get("risks", [])
                action_items = parsed.get("action_items", [])
                # key_pointsが存在する場合、summaryにtitleも付与
                title = parsed.get("title", "")
                if title and summary:
                    summary = f"{title}\n\n{summary}"
            else:
                # パース失敗 → bulletにフォールバック
                logger.warning(
                    "llm_summarizer: structured parse failed, falling back to bullet. "
                    "company_id=%s raw=%s",
                    inp.company_id,
                    raw_content[:200],
                )
                summary = raw_content
                actual_style = "bullet"
        else:
            summary = raw_content

        summary_length = len(summary)
        compression_ratio = round(summary_length / original_length, 4) if original_length > 0 else 0.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=_AGENT_NAME,
            success=True,
            result={
                "summary": summary,
                "original_length": original_length,
                "summary_length": summary_length,
                "compression_ratio": compression_ratio,
                "style": actual_style,
                "key_points": key_points,
                "risks": risks,
                "action_items": action_items,
            },
            confidence=0.8,
            cost_yen=response.cost_yen,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error("llm_summarizer error: %s", e)
        return MicroAgentOutput(
            agent_name=_AGENT_NAME,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

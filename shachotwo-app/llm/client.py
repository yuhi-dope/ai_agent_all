"""Multi-provider LLM client with automatic fallback and cost tracking."""
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic
import openai
from google import genai
from google.genai import types

from llm.cost_tracker import get_cost_tracker
from llm.model_registry import get_fallback_chain, get_model_costs, select_optimal_model

# 汎用Enumは shared.enums に一元化。後方互換のため再エクスポート。
from shared.enums import ModelTier  # noqa: F401

logger = logging.getLogger(__name__)

# エクスポネンシャルバックオフでリトライすべきエラー種別
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES_PER_MODEL: int = 2
_BACKOFF_BASE_SECONDS: float = 1.0

# ReasoningTrace付きレスポンスに追加するプロンプト指示
_REASONING_TRACE_INSTRUCTION = """

（レスポンスの末尾に以下のJSONブロックを追加してください。メインの回答テキストの後に続けてください）
```json
{
  "reasoning_trace": {
    "action_summary": "実施した処理の1文要約（例: 積算金額を¥12,650,000と算出しました）",
    "confidence_score": 0.0,
    "evidence": [
      {"description": "根拠の説明", "source": "unit_price_master", "confidence": 0.9, "value": "具体的な値（任意）"}
    ],
    "data_sources": ["参照したデータソース名"],
    "assumptions": ["前提条件・不確かな点"],
    "alternatives": ["他の選択肢（あれば）"]
  }
}
```"""


@dataclass
class Evidence:
    """推論根拠の1件。"""
    description: str        # 根拠の説明
    source: str             # "unit_price_master" / "similar_case" / "genome_rule" 等
    confidence: float       # 0.0-1.0
    value: Optional[str] = None  # 具体的な値（金額、件数等）


@dataclass
class ReasoningTrace:
    """LLMの推論過程を構造化した説明可能性データ。"""
    action_summary: str                          # 「積算金額を¥12,650,000と算出しました」
    confidence_score: float                      # 0.0-1.0（全体の確信度）
    evidence: list[Evidence] = field(default_factory=list)   # 根拠リスト
    data_sources: list[str] = field(default_factory=list)    # 参照したデータソース名
    assumptions: list[str] = field(default_factory=list)     # 前提条件・不確かな点
    alternatives: list[str] = field(default_factory=list)    # 他の選択肢（あれば）


def _parse_reasoning_trace(content: str) -> tuple[str, Optional[ReasoningTrace]]:
    """
    LLMレスポンスの末尾にある reasoning_trace JSONブロックを抽出・パースする。

    Returns:
        (clean_content, trace): JSONブロックを除いた本文とReasoningTrace（パース失敗時はNone）
    """
    # ```json ... ``` ブロックを末尾から探す
    pattern = r'```json\s*(\{.*?"reasoning_trace".*?\})\s*```'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return content, None

    json_str = match.group(1)
    clean_content = content[: match.start()].rstrip()

    try:
        data = json.loads(json_str)
        rt_data = data.get("reasoning_trace", {})

        evidence_list: list[Evidence] = []
        for e in rt_data.get("evidence", []):
            if isinstance(e, dict):
                evidence_list.append(Evidence(
                    description=str(e.get("description", "")),
                    source=str(e.get("source", "unknown")),
                    confidence=float(e.get("confidence", 0.0)),
                    value=str(e["value"]) if e.get("value") is not None else None,
                ))

        trace = ReasoningTrace(
            action_summary=str(rt_data.get("action_summary", "")),
            confidence_score=float(rt_data.get("confidence_score", 0.0)),
            evidence=evidence_list,
            data_sources=[str(s) for s in rt_data.get("data_sources", [])],
            assumptions=[str(a) for a in rt_data.get("assumptions", [])],
            alternatives=[str(a) for a in rt_data.get("alternatives", [])],
        )
        return clean_content, trace

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("ReasoningTrace JSONパース失敗 (無視してNoneを返します): %s", exc)
        return content, None


@dataclass
class LLMTask:
    """A task to send to the LLM."""
    messages: list[dict[str, str]]
    tier: ModelTier = ModelTier.FAST
    max_tokens: int = 2048
    temperature: float = 0.2
    company_id: Optional[str] = None
    task_type: str = "unknown"
    response_format: Optional[dict] = None
    requires_vision: bool = False               # ビジョン（画像入力）が必要か
    requires_structured_output: bool = False    # 構造化JSON出力が必要か
    model_override: Optional[str] = None        # 特定モデルを直接指定
    tools: Optional[list[dict[str, Any]]] = None  # Tool Use 定義（Anthropic tools 形式）
    with_trace: bool = False                    # 推論トレース（ReasoningTrace）を返すか


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    model_used: str
    tokens_in: int
    tokens_out: int
    cost_yen: float
    latency_ms: int
    fallback_used: bool = False
    fallback_from: Optional[str] = None
    reasoning_trace: Optional[ReasoningTrace] = None


def _is_retryable_exception(exc: Exception) -> bool:
    """429 / 5xx 相当のエラーのみリトライ対象と判定する。"""
    exc_str = str(exc)
    # anthropic / openai SDK は status_code 属性を持つことが多い
    status_code: Optional[int] = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code in _RETRYABLE_STATUS_CODES
    # Google genai SDK は例外メッセージに HTTP ステータスが含まれる場合がある
    for code in _RETRYABLE_STATUS_CODES:
        if str(code) in exc_str:
            return True
    return False


class LLMClient:
    """Multi-provider LLM client with automatic fallback and cost tracking."""

    def __init__(self) -> None:
        self._gemini_client: Optional[genai.Client] = None
        self._anthropic_client: Optional[anthropic.AsyncAnthropic] = None
        self._openai_client: Optional[openai.AsyncOpenAI] = None

    # ------------------------------------------------------------------
    # クライアント初期化ヘルパー
    # ------------------------------------------------------------------

    def _ensure_gemini(self) -> genai.Client:
        if self._gemini_client is None:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set")
            self._gemini_client = genai.Client(api_key=api_key)
        return self._gemini_client

    def _ensure_anthropic(self) -> anthropic.AsyncAnthropic:
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.AsyncAnthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
            )
        return self._anthropic_client

    def _ensure_openai(self) -> openai.AsyncOpenAI:
        if self._openai_client is None:
            self._openai_client = openai.AsyncOpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
        return self._openai_client

    # ------------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------------

    async def generate(self, task: LLMTask) -> LLMResponse:
        """Generate a response with automatic fallback on failure.

        フォールバックチェインは model_registry から動的に取得する。
        同一モデル内で 429 / 5xx が発生した場合はエクスポネンシャルバックオフで
        最大 _MAX_RETRIES_PER_MODEL 回リトライしてから次モデルへ移行する。

        with_trace=True の場合、メッセージの最後のユーザーターンに推論トレース指示を
        追加してLLMに構造化JSONを出力させ、LLMResponseのreasoning_traceに格納する。
        """
        cost_tracker = get_cost_tracker()
        if task.company_id:
            cost_tracker.check_budget(task.company_id)

        # with_trace=True の場合はメッセージを拡張（元のtaskを変更しない）
        effective_task = task
        if task.with_trace:
            extended_messages = list(task.messages)
            # 最後のユーザーメッセージに推論トレース指示を追記
            for idx in range(len(extended_messages) - 1, -1, -1):
                if extended_messages[idx].get("role") == "user":
                    extended_messages[idx] = dict(extended_messages[idx])
                    extended_messages[idx]["content"] = (
                        extended_messages[idx]["content"] + _REASONING_TRACE_INSTRUCTION
                    )
                    break
            else:
                # ユーザーターンがない場合は末尾に追加
                extended_messages.append({
                    "role": "user",
                    "content": _REASONING_TRACE_INSTRUCTION.strip(),
                })
            # 元のtaskを変更せずに新しいオブジェクトを作成
            from dataclasses import replace
            effective_task = replace(task, messages=extended_messages)

        # フォールバックチェイン構築
        if effective_task.model_override:
            chain: list[str] = [effective_task.model_override]
        else:
            primary = select_optimal_model(
                tier=effective_task.tier.value,
                requires_vision=effective_task.requires_vision,
                requires_structured_output=effective_task.requires_structured_output,
            )
            fallback_chain = get_fallback_chain(effective_task.tier.value)
            # primary が先頭になるよう重複を排除して結合
            chain = [primary] + [m for m in fallback_chain if m != primary]

        first_model = chain[0]
        last_error: Optional[Exception] = None

        for i, model_id in enumerate(chain):
            for attempt in range(_MAX_RETRIES_PER_MODEL):
                try:
                    start_ms = int(time.time() * 1000)
                    result = await asyncio.wait_for(
                        self._call_model(model_id, effective_task),
                        timeout=30.0,
                    )
                    latency_ms = int(time.time() * 1000) - start_ms

                    costs = get_model_costs(model_id)
                    cost_yen = (
                        result["tokens_in"] / 1000 * costs["in"]
                        + result["tokens_out"] / 1000 * costs["out"]
                    )

                    # with_trace=True の場合はレスポンスからReasoningTraceを抽出
                    raw_content: str = result["content"]
                    reasoning_trace: Optional[ReasoningTrace] = None
                    if task.with_trace:
                        raw_content, reasoning_trace = _parse_reasoning_trace(raw_content)

                    response = LLMResponse(
                        content=raw_content,
                        model_used=model_id,
                        tokens_in=result["tokens_in"],
                        tokens_out=result["tokens_out"],
                        cost_yen=round(cost_yen, 4),
                        latency_ms=latency_ms,
                        fallback_used=i > 0,
                        fallback_from=first_model if i > 0 else None,
                        reasoning_trace=reasoning_trace,
                    )

                    if i > 0:
                        logger.warning(
                            "LLM fallback: %s -> %s (task=%s, company=%s)",
                            first_model, model_id, task.task_type, task.company_id,
                        )

                    if task.company_id:
                        cost_tracker.record_cost(task.company_id, response.cost_yen)

                    logger.info(
                        "LLM call: model=%s tokens=%d+%d cost=¥%.4f latency=%dms task=%s trace=%s",
                        model_id, result["tokens_in"], result["tokens_out"],
                        cost_yen, latency_ms, task.task_type,
                        "yes" if reasoning_trace else "no",
                    )
                    return response

                except Exception as exc:
                    last_error = exc
                    if _is_retryable_exception(exc) and attempt < _MAX_RETRIES_PER_MODEL - 1:
                        backoff = _BACKOFF_BASE_SECONDS * (2 ** attempt)
                        logger.warning(
                            "LLM retryable error (%s) attempt=%d/%d, backoff=%.1fs: %s",
                            model_id, attempt + 1, _MAX_RETRIES_PER_MODEL, backoff, exc,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    # リトライ不要 or リトライ上限に達した → 次モデルへ
                    logger.error("LLM error (%s): %s", model_id, exc)
                    break

        raise RuntimeError(
            f"All models in {task.tier.value} chain failed. Last error: {last_error}"
        )

    # ------------------------------------------------------------------
    # 内部ディスパッチ
    # ------------------------------------------------------------------

    async def _call_model(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        if model_id.startswith("gemini"):
            return await self._call_gemini(model_id, task)
        if model_id.startswith("claude"):
            return await self._call_anthropic(model_id, task)
        if model_id.startswith("gpt"):
            return await self._call_openai(model_id, task)
        raise ValueError(f"Unknown model: {model_id}")

    # ------------------------------------------------------------------
    # プロバイダー別呼び出し
    # ------------------------------------------------------------------

    async def _call_gemini(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        client = self._ensure_gemini()

        system_msg: Optional[str] = None
        contents: list[types.Content] = []
        for msg in task.messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                role = "model" if msg["role"] == "assistant" else msg["role"]
                contents.append(
                    types.Content(role=role, parts=[types.Part(text=msg["content"])])
                )

        config_kwargs: dict[str, Any] = {
            "system_instruction": system_msg,
            "max_output_tokens": task.max_tokens,
            "temperature": task.temperature,
        }

        # structured output 対応: response_format が指定されている場合
        if task.response_format is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = task.response_format

        config = types.GenerateContentConfig(**config_kwargs)

        response = await client.aio.models.generate_content(
            model=model_id,
            contents=contents,
            config=config,
        )

        return {
            "content": response.text,
            "tokens_in": response.usage_metadata.prompt_token_count,
            "tokens_out": response.usage_metadata.candidates_token_count,
        }

    async def _call_anthropic(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        client = self._ensure_anthropic()

        # Anthropic プロンプトキャッシング戦略:
        # cache_control: {"type": "ephemeral"} を付与すると、そのブロックまでの
        # 入力がキャッシュされ、5分TTL内の再リクエストでキャッシュヒットする。
        # キャッシュブレークポイントは最大4箇所設定可能。
        #
        # 優先順位（上から順にキャッシュ効果が高い）:
        #   1. system prompt — 全リクエストで共通、最もヒット率が高い
        #   2. tools 定義 — Tool Use 利用時に毎回送信される大きなペイロード
        #   3. 会話履歴の先頭部分 — 長い会話で冒頭が変わらない場合に有効
        #
        # 最小キャッシュ要件: 約1024トークン（日本語2000文字で近似）

        _CACHE_CHAR_THRESHOLD = 2000
        _CACHE_CONTROL = {"type": "ephemeral"}

        system_msg: Optional[str] = None
        messages: list[dict[str, Any]] = []
        for msg in task.messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        kwargs: dict[str, Any] = {
            "model": model_id,
            "max_tokens": task.max_tokens,
            "temperature": task.temperature,
            "messages": messages,
        }

        # --- ブレークポイント1: system prompt キャッシュ ---
        if system_msg:
            if len(system_msg) > _CACHE_CHAR_THRESHOLD:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system_msg,
                        "cache_control": _CACHE_CONTROL,
                    }
                ]
                logger.debug(
                    "Anthropic prompt caching enabled for system prompt "
                    "(len=%d chars, model=%s)",
                    len(system_msg), model_id,
                )
            else:
                kwargs["system"] = system_msg

        # --- ブレークポイント2: tools 定義キャッシュ ---
        # Tool Use 定義が大きい場合、最後の tool に cache_control を付与して
        # system + tools 全体をキャッシュ対象にする。
        if task.tools:
            tools_with_cache = [t.copy() for t in task.tools]
            tools_json_len = sum(len(str(t)) for t in tools_with_cache)
            if tools_json_len > _CACHE_CHAR_THRESHOLD:
                tools_with_cache[-1]["cache_control"] = _CACHE_CONTROL
                logger.debug(
                    "Anthropic prompt caching enabled for tools "
                    "(count=%d, est_len=%d chars, model=%s)",
                    len(tools_with_cache), tools_json_len, model_id,
                )
            kwargs["tools"] = tools_with_cache

        # --- ブレークポイント3: 会話履歴キャッシュ（Phase 2+） ---
        # 長い会話の先頭N件が不変の場合、途中にブレークポイントを挿入すると
        # 差分のみが課金対象になる。現在は未適用。
        # 実装時: messages の適切な位置の content を
        #   [{"type": "text", "text": msg, "cache_control": _CACHE_CONTROL}]
        # に変換する。

        response = await client.messages.create(**kwargs)

        # キャッシュ利用状況をログ出力
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cache_read > 0 or cache_creation > 0:
            logger.info(
                "Anthropic cache stats: read=%d tokens, creation=%d tokens "
                "(model=%s, task=%s)",
                cache_read, cache_creation, model_id, task.task_type,
            )

        return {
            "content": response.content[0].text,
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
        }

    async def _call_openai(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        client = self._ensure_openai()

        response = await client.chat.completions.create(
            model=model_id,
            messages=task.messages,  # type: ignore[arg-type]
            max_tokens=task.max_tokens,
            temperature=task.temperature,
        )

        return {
            "content": response.choices[0].message.content,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
        }


# ------------------------------------------------------------------
# シングルトン
# ------------------------------------------------------------------

_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get the singleton LLM client instance."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

"""Multi-provider LLM client with automatic fallback and cost tracking."""
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import anthropic
import google.generativeai as genai
import openai

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    FAST = "fast"          # Gemini 2.5 Flash — extraction, Q&A, routine
    STANDARD = "standard"  # Gemini 2.5 Pro — complex reasoning
    PREMIUM = "premium"    # Claude Opus — critical decisions


FALLBACK_CHAINS: dict[ModelTier, list[str]] = {
    ModelTier.FAST:     ["gemini-2.5-flash", "gemini-2.5-pro", "claude-opus-4-5"],
    ModelTier.STANDARD: ["gemini-2.5-pro",   "claude-opus-4-5", "gpt-4o"],
    ModelTier.PREMIUM:  ["claude-opus-4-5",  "gpt-4o",          "gemini-2.5-pro"],
}

# Cost per 1K tokens (JPY)
MODEL_COSTS: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"in": 0.011, "out": 0.044},
    "gemini-2.5-pro":   {"in": 0.184, "out": 0.735},
    "claude-opus-4-5":  {"in": 2.25,  "out": 11.25},
    "gpt-4o":           {"in": 0.375, "out": 1.5},
}


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


class LLMClient:
    """Multi-provider LLM client with automatic fallback and cost tracking."""

    def __init__(self):
        self._gemini_configured = False
        self._anthropic_client: Optional[anthropic.AsyncAnthropic] = None
        self._openai_client: Optional[openai.AsyncOpenAI] = None

    def _ensure_gemini(self):
        if not self._gemini_configured:
            api_key = os.environ.get("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                self._gemini_configured = True
            else:
                raise RuntimeError("GEMINI_API_KEY not set")

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

    async def generate(self, task: LLMTask) -> LLMResponse:
        """Generate a response with automatic fallback on failure."""
        chain = FALLBACK_CHAINS[task.tier]
        last_error: Optional[Exception] = None

        for i, model_id in enumerate(chain):
            try:
                start_ms = int(time.time() * 1000)
                result = await self._call_model(model_id, task)
                latency_ms = int(time.time() * 1000) - start_ms

                costs = MODEL_COSTS.get(model_id, {"in": 0, "out": 0})
                cost_yen = (
                    result["tokens_in"] / 1000 * costs["in"]
                    + result["tokens_out"] / 1000 * costs["out"]
                )

                response = LLMResponse(
                    content=result["content"],
                    model_used=model_id,
                    tokens_in=result["tokens_in"],
                    tokens_out=result["tokens_out"],
                    cost_yen=round(cost_yen, 4),
                    latency_ms=latency_ms,
                    fallback_used=i > 0,
                    fallback_from=chain[0] if i > 0 else None,
                )

                if i > 0:
                    logger.warning(
                        f"LLM fallback: {chain[0]} → {model_id} "
                        f"(task={task.task_type}, company={task.company_id})"
                    )

                logger.info(
                    f"LLM call: model={model_id} tokens={result['tokens_in']}+{result['tokens_out']} "
                    f"cost=¥{cost_yen:.4f} latency={latency_ms}ms task={task.task_type}"
                )
                return response

            except Exception as e:
                last_error = e
                logger.error(f"LLM error ({model_id}): {e}")
                continue

        raise RuntimeError(
            f"All models in {task.tier.value} chain failed. Last error: {last_error}"
        )

    async def _call_model(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        if model_id.startswith("gemini"):
            return await self._call_gemini(model_id, task)
        elif model_id.startswith("claude"):
            return await self._call_anthropic(model_id, task)
        elif model_id.startswith("gpt"):
            return await self._call_openai(model_id, task)
        raise ValueError(f"Unknown model: {model_id}")

    async def _call_gemini(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        self._ensure_gemini()

        system_msg = None
        contents = []
        for msg in task.messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                contents.append({"role": msg["role"], "parts": [msg["content"]]})

        model = genai.GenerativeModel(
            model_id,
            system_instruction=system_msg,
        )

        config = genai.GenerationConfig(
            max_output_tokens=task.max_tokens,
            temperature=task.temperature,
        )

        response = await model.generate_content_async(
            contents,
            generation_config=config,
        )

        return {
            "content": response.text,
            "tokens_in": response.usage_metadata.prompt_token_count,
            "tokens_out": response.usage_metadata.candidates_token_count,
        }

    async def _call_anthropic(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        client = self._ensure_anthropic()

        system_msg = None
        messages = []
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
        if system_msg:
            kwargs["system"] = system_msg

        response = await client.messages.create(**kwargs)

        return {
            "content": response.content[0].text,
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
        }

    async def _call_openai(self, model_id: str, task: LLMTask) -> dict[str, Any]:
        client = self._ensure_openai()

        response = await client.chat.completions.create(
            model=model_id,
            messages=task.messages,
            max_tokens=task.max_tokens,
            temperature=task.temperature,
        )

        return {
            "content": response.choices[0].message.content,
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
        }


# Singleton
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get the singleton LLM client instance."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client

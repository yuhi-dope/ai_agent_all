"""Q&A engine: search → context → LLM answer generation."""
import json
import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from brain.knowledge.search import SearchResult, hybrid_search
from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.extraction import SYSTEM_QA

logger = logging.getLogger(__name__)


class SourceInfo(BaseModel):
    knowledge_id: UUID
    title: str
    relevance: float
    excerpt: str | None = None


class QAResult(BaseModel):
    answer: str
    confidence: float
    sources: list[SourceInfo]
    missing_info: str | None = None
    model_used: str
    cost_yen: float


async def answer_question(
    question: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
) -> QAResult:
    """Search knowledge base and generate an LLM answer."""
    # 1. Search
    results = await hybrid_search(question, company_id, department, top_k)

    if not results:
        return QAResult(
            answer="関連するナレッジが登録されていません。先にナレッジを入力してください。",
            confidence=0.0,
            sources=[],
            missing_info="ナレッジベースにデータがありません",
            model_used="none",
            cost_yen=0.0,
        )

    # 2. Build context
    context = _build_context(results)

    # 3. LLM answer
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": SYSTEM_QA},
            {"role": "user", "content": f"## ナレッジベース\n{context}\n\n## 質問\n{question}"},
        ],
        tier=ModelTier.FAST,
        task_type="qa",
        company_id=company_id,
    ))

    # 4. Parse response
    return _parse_qa_response(response.content, response.model_used, response.cost_yen, results)


def _build_context(results: list[SearchResult]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[{i}] {r.title} (部署: {r.department}, カテゴリ: {r.category}, "
            f"類似度: {r.similarity:.2f})\n{r.content}"
        )
    return "\n\n".join(parts)


def _parse_qa_response(
    content: str,
    model_used: str,
    cost_yen: float,
    search_results: list[SearchResult],
) -> QAResult:
    """Parse LLM JSON response, with plain-text fallback."""
    try:
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        # Always use search results for sources (LLM can't reliably return UUIDs)
        sources = [
            SourceInfo(
                knowledge_id=r.item_id,
                title=r.title,
                relevance=r.similarity,
            )
            for r in search_results
        ]

        return QAResult(
            answer=data.get("answer", content),
            confidence=data.get("confidence", 0.5),
            sources=sources,
            missing_info=data.get("missing_info"),
            model_used=model_used,
            cost_yen=cost_yen,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"QA response JSON parse failed: {e}, using plain text fallback")
        return QAResult(
            answer=content,
            confidence=0.5,
            sources=[
                SourceInfo(knowledge_id=r.item_id, title=r.title, relevance=r.similarity)
                for r in search_results
            ],
            model_used=model_used,
            cost_yen=cost_yen,
        )

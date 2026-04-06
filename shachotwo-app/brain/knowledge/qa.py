"""Q&A engine: search → context → LLM answer generation."""
import json
import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from brain.knowledge.search import SearchResult, enhanced_search, hybrid_search
from db.supabase import get_service_client
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
    search_mode: str = "hybrid"  # "enhanced" or "hybrid"


async def answer_question(
    question: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
    use_enhanced_search: bool = True,
) -> QAResult:
    """Search knowledge base and generate an LLM answer.

    Args:
        question: ユーザーの質問文
        company_id: テナントID
        department: 部署フィルタ（任意）
        top_k: 取得する検索結果件数
        use_enhanced_search: Trueのとき enhanced_search（クエリ拡張+リランク）を使用。
                             Falseのとき従来の hybrid_search（速度優先）を使用。
    """
    # 1. Search
    if use_enhanced_search:
        results = await enhanced_search(question, company_id, department, top_k)
        search_mode = "enhanced"
    else:
        results = await hybrid_search(question, company_id, department, top_k)
        search_mode = "hybrid"

    if not results:
        return QAResult(
            answer="関連するナレッジが登録されていません。先にナレッジを入力してください。",
            confidence=0.0,
            sources=[],
            missing_info="ナレッジベースにデータがありません",
            model_used="none",
            cost_yen=0.0,
            search_mode=search_mode,
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
    qa_result = _parse_qa_response(
        response.content, response.model_used, response.cost_yen, results, search_mode
    )

    # 5. qa_usage_count をインクリメント（ファイアアンドフォーゲット）
    _increment_usage_counts(results)

    return qa_result


def _increment_usage_counts(results: list[SearchResult]) -> None:
    """検索で引用されたナレッジの qa_usage_count を非同期でインクリメントする。
    失敗しても本体の回答には影響しない。
    """
    if not results:
        return
    try:
        db = get_service_client()
        item_ids = [str(r.item_id) for r in results]
        db.rpc("increment_qa_usage_count", {"item_ids": item_ids}).execute()
    except Exception as e:
        logger.warning(f"qa_usage_count increment failed (non-critical): {e}")


def _calc_confidence(results: list[SearchResult], llm_confidence: float = 0.5) -> float:
    """ベクトル検索ベース + LLM微調整で信頼度を算出する。

    ベクトル検索（客観的）を7割、LLM自己評価（主観的）を3割で配分。
    LLM自己評価は自信過剰になりがちなので0.85でキャップする。

    構成:
    - ベクトル検索スコア (70%): max_sim×0.4 + avg_top3×0.2 + 件数ボーナス×0.1
    - LLM自己評価 (30%): min(llm_confidence, 0.85) × 0.3
    """
    if not results:
        return 0.0

    max_sim = max(r.similarity for r in results)
    top3 = sorted([r.similarity for r in results], reverse=True)[:3]
    avg_top3 = sum(top3) / len(top3)

    # ソース件数ボーナス: 1件=0.2, 3件=0.6, 5件以上=1.0
    count_bonus = min(len(results) / 5.0, 1.0)

    # ベクトル検索ベース (70%)
    vector_score = max_sim * 0.4 + avg_top3 * 0.2 + count_bonus * 0.1

    # LLM自己評価 (30%) — 0.85でキャップして自信過剰を抑制
    llm_score = min(llm_confidence, 0.85) * 0.3

    confidence = vector_score + llm_score
    return round(max(0.0, min(1.0, confidence)), 2)


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
    search_mode: str = "hybrid",
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
            confidence=_calc_confidence(search_results, data.get("confidence", 0.5)),
            sources=sources,
            missing_info=data.get("missing_info"),
            model_used=model_used,
            cost_yen=cost_yen,
            search_mode=search_mode,
        )

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"QA response JSON parse failed: {e}, using plain text fallback")
        return QAResult(
            answer=content,
            confidence=_calc_confidence(search_results),
            sources=[
                SourceInfo(knowledge_id=r.item_id, title=r.title, relevance=r.similarity)
                for r in search_results
            ],
            model_used=model_used,
            cost_yen=cost_yen,
            search_mode=search_mode,
        )

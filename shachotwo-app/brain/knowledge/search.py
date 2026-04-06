"""Vector search + keyword fallback for knowledge items."""
import json
import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from brain.knowledge.embeddings import generate_query_embedding
from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

# クエリ拡張用プロンプト
_SYSTEM_QUERY_EXPAND = """あなたは企業ナレッジベースの検索を補助するアシスタントです。
ユーザーの質問に対して、その質問への回答として含まれうる内容を1〜2文で予測してください。
実際のデータがなくても構いません。検索精度を上げるための仮想的な回答文を生成してください。
簡潔に、日本語で出力してください。"""

# リランキング用プロンプト
_SYSTEM_RERANK = """あなたは検索結果の関連度を評価する専門家です。
ユーザーの質問に対して、各検索結果の関連度を0.0〜1.0で評価してください。

出力形式（JSONのみ。説明不要）:
[{"index": 0, "relevance": 0.9}, {"index": 1, "relevance": 0.3}, ...]

評価基準:
- 1.0: 質問に直接答えている
- 0.7〜0.9: 関連情報を含む
- 0.4〜0.6: 間接的に関連
- 0.0〜0.3: ほぼ無関係"""


class SearchResult(BaseModel):
    """A single search result."""
    item_id: UUID
    title: str
    content: str
    department: str
    category: str
    item_type: str
    confidence: float | None
    similarity: float


async def vector_search(
    query: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
    similarity_threshold: float = 0.5,
) -> list[SearchResult]:
    """Search knowledge items using pgvector cosine similarity."""
    query_embedding = await generate_query_embedding(query)
    db = get_service_client()

    result = db.rpc("match_knowledge_items", {
        "query_embedding": query_embedding,
        "match_company_id": company_id,
        "match_department": department,
        "match_threshold": similarity_threshold,
        "match_count": top_k,
    }).execute()

    return [
        SearchResult(
            item_id=row["id"],
            title=row["title"],
            content=row["content"],
            department=row["department"],
            category=row["category"],
            item_type=row["item_type"],
            confidence=row.get("confidence"),
            similarity=row["similarity"],
        )
        for row in (result.data or [])
    ]


async def keyword_search(
    query: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
) -> list[SearchResult]:
    """Keyword fallback search using ILIKE."""
    db = get_service_client()
    q = db.table("knowledge_items") \
        .select("id, title, content, department, category, item_type, confidence") \
        .eq("company_id", company_id) \
        .eq("is_active", True)

    if department:
        q = q.eq("department", department)

    # Search in title and content
    q = q.or_(f"title.ilike.%{query}%,content.ilike.%{query}%")
    result = q.limit(top_k).execute()

    return [
        SearchResult(
            item_id=row["id"],
            title=row["title"],
            content=row["content"],
            department=row["department"],
            category=row["category"],
            item_type=row["item_type"],
            confidence=row.get("confidence"),
            similarity=0.5,  # fixed score for keyword matches
        )
        for row in (result.data or [])
    ]


async def expand_query(query: str, company_id: str) -> str:
    """クエリ拡張: LLMで仮想的な回答テキストを生成してembedding精度を上げる。

    HyDE (Hypothetical Document Embeddings) の簡易版。
    元クエリ + 仮想回答を結合した文字列を返す。
    LLM呼び出しに失敗した場合は元クエリをそのまま返す。
    """
    llm = get_llm_client()
    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_QUERY_EXPAND},
                {"role": "user", "content": query},
            ],
            tier=ModelTier.FAST,
            max_tokens=128,
            temperature=0.3,
            task_type="query_expand",
            company_id=company_id,
        ))
        hypothetical = response.content.strip()
        expanded = f"{query} {hypothetical}"
        logger.debug(f"Query expanded: '{query}' → '{expanded[:80]}...'")
        return expanded
    except Exception as e:
        logger.warning(f"expand_query failed, using original query: {e}")
        return query


async def rerank_results(
    query: str,
    results: list[SearchResult],
    company_id: str,
    top_n: int = 5,
) -> list[SearchResult]:
    """LLMで検索結果を再ランキングして上位top_nを返す。

    LLM呼び出しに失敗した場合はフォールバックとして元のresultsをそのまま返す。
    """
    if not results:
        return results

    # LLMに渡す候補リストを構築
    candidates_text = "\n\n".join(
        f"[{i}] タイトル: {r.title}\n内容: {r.content[:200]}"
        for i, r in enumerate(results)
    )
    user_content = (
        f"## 質問\n{query}\n\n"
        f"## 検索結果候補\n{candidates_text}"
    )

    llm = get_llm_client()
    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_RERANK},
                {"role": "user", "content": user_content},
            ],
            tier=ModelTier.FAST,
            max_tokens=256,
            temperature=0.1,
            task_type="rerank",
            company_id=company_id,
        ))

        text = response.content.strip()
        # コードブロック除去
        if text.startswith("```"):
            lines = text.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        scores: list[dict] = json.loads(text)
        # index → relevance のマップを作る
        score_map: dict[int, float] = {
            item["index"]: float(item["relevance"])
            for item in scores
            if isinstance(item.get("index"), int) and isinstance(item.get("relevance"), (int, float))
        }

        # relevanceスコアで並び替え（スコアなしは0.0）
        ranked = sorted(
            results,
            key=lambda r: score_map.get(results.index(r), 0.0),
            reverse=True,
        )
        return ranked[:top_n]

    except Exception as e:
        logger.warning(f"rerank_results failed, returning original results: {e}")
        return results[:top_n]


async def enhanced_search(
    query: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
) -> list[SearchResult]:
    """精度優先の3段階RAGパイプライン。

    1. クエリ拡張 (HyDE簡易版)
    2. ベクトル検索 (top_k=10固定)
    3. LLMリランキング → top_k に絞る
    """
    expanded = await expand_query(query, company_id)
    # リランクのため多めに取得（固定10件）
    candidates = await vector_search(expanded, company_id, department, top_k=10)

    if not candidates:
        # フォールバック: 元クエリでhybrid_search
        logger.info("enhanced_search: no vector results, falling back to hybrid_search")
        return await hybrid_search(query, company_id, department, top_k)

    return await rerank_results(query, candidates, company_id, top_n=top_k)


async def hybrid_search(
    query: str,
    company_id: str,
    department: str | None = None,
    top_k: int = 5,
) -> list[SearchResult]:
    """Vector search with keyword fallback when results are insufficient."""
    results = await vector_search(query, company_id, department, top_k)

    if len(results) < top_k:
        remaining = top_k - len(results)
        existing_ids = {r.item_id for r in results}
        kw_results = await keyword_search(query, company_id, department, remaining + 5)
        for r in kw_results:
            if r.item_id not in existing_ids and len(results) < top_k:
                results.append(r)
                existing_ids.add(r.item_id)

    return results

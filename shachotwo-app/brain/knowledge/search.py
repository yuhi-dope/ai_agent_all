"""Vector search + keyword fallback for knowledge items."""
import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from brain.knowledge.embeddings import generate_query_embedding
from db.supabase import get_service_client

logger = logging.getLogger(__name__)


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

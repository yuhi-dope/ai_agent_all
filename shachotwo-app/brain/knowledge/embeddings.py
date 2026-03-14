"""Gemini embedding generation for knowledge items."""
import logging
import os
from typing import Optional

import google.generativeai as genai

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
DIMENSIONS = 768

_configured = False


def _ensure_configured():
    global _configured
    if not _configured:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        _configured = True


async def generate_embedding(text: str) -> list[float]:
    """Generate embedding for a single text."""
    _ensure_configured()
    result = genai.embed_content(
        model=f"models/{EMBEDDING_MODEL}",
        content=text,
        task_type="retrieval_document",
        output_dimensionality=DIMENSIONS,
    )
    return result["embedding"]


async def generate_query_embedding(text: str) -> list[float]:
    """Generate embedding for a search query."""
    _ensure_configured()
    result = genai.embed_content(
        model=f"models/{EMBEDDING_MODEL}",
        content=text,
        task_type="retrieval_query",
        output_dimensionality=DIMENSIONS,
    )
    return result["embedding"]


async def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding generation."""
    _ensure_configured()
    all_embeddings: list[list[float]] = []
    batch_size = 100

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        result = genai.embed_content(
            model=f"models/{EMBEDDING_MODEL}",
            content=batch,
            task_type="retrieval_document",
            output_dimensionality=DIMENSIONS,
        )
        all_embeddings.extend(result["embedding"])

    return all_embeddings


async def update_item_embedding(item_id: str, company_id: str) -> None:
    """Generate and save embedding for a single knowledge_item."""
    db = get_service_client()
    row = db.table("knowledge_items").select("title, content").eq("id", item_id).single().execute()
    text = f"{row.data['title']}\n{row.data['content']}"
    embedding = await generate_embedding(text)
    db.table("knowledge_items").update({"embedding": embedding}).eq("id", item_id).execute()
    logger.info(f"Updated embedding for item {item_id}")


async def backfill_embeddings(company_id: str, batch_size: int = 50) -> int:
    """Generate embeddings for items that don't have one yet. Returns count processed."""
    db = get_service_client()
    result = db.table("knowledge_items") \
        .select("id, title, content") \
        .eq("company_id", company_id) \
        .is_("embedding", "null") \
        .eq("is_active", True) \
        .limit(batch_size) \
        .execute()

    if not result.data:
        return 0

    texts = [f"{r['title']}\n{r['content']}" for r in result.data]
    embeddings = await generate_embeddings(texts)

    for row, emb in zip(result.data, embeddings):
        db.table("knowledge_items").update({"embedding": emb}).eq("id", row["id"]).execute()

    logger.info(f"Backfilled {len(result.data)} embeddings for company {company_id}")
    return len(result.data)

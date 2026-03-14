"""Text ingestion — delegates to extraction pipeline."""
from brain.extraction import ExtractionResult, extract_knowledge


async def ingest_text(
    content: str,
    company_id: str,
    user_id: str,
    department: str | None = None,
    category: str | None = None,
) -> ExtractionResult:
    """Ingest raw text → extract knowledge items.

    Thin wrapper around brain.extraction.extract_knowledge.
    Exists as the public API entry point for text ingestion.
    """
    return await extract_knowledge(
        text=content,
        company_id=company_id,
        user_id=user_id,
        department=department,
        category=category,
    )

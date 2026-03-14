"""Text → LLM structured extraction → knowledge_items save pipeline."""
import json
import logging
from uuid import UUID

from brain.extraction.models import ExtractedItem, ExtractionResult
from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.extraction import SYSTEM_EXTRACTION

logger = logging.getLogger(__name__)

VALID_ITEM_TYPES = {"rule", "flow", "decision_logic", "fact", "tip"}


async def extract_knowledge(
    text: str,
    company_id: str,
    user_id: str,
    department: str | None = None,
    category: str | None = None,
) -> ExtractionResult:
    """Run the full extraction pipeline.

    1. Create knowledge_session (status=processing)
    2. Call LLM with SYSTEM_EXTRACTION prompt
    3. Parse JSON → ExtractedItem list
    4. Insert each item into knowledge_items
    5. Update session status to completed
    """
    client = get_service_client()
    session_id = await _create_session(client, company_id, user_id, text)

    try:
        # LLM extraction
        llm = get_llm_client()
        user_prompt = _build_user_prompt(text, department, category)
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": SYSTEM_EXTRACTION},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.FAST,
            task_type="extraction",
            company_id=company_id,
        ))

        # Parse LLM response
        items = _parse_items(response.content)
        if items is None:
            # Retry once with explicit JSON instruction
            logger.warning("JSON parse failed, retrying with explicit instruction")
            retry_response = await llm.generate(LLMTask(
                messages=[
                    {"role": "system", "content": SYSTEM_EXTRACTION},
                    {"role": "user", "content": user_prompt + "\n\n必ずJSON配列形式で出力してください。マークダウンのコードブロックは不要です。"},
                ],
                tier=ModelTier.FAST,
                task_type="extraction_retry",
                company_id=company_id,
            ))
            items = _parse_items(retry_response.content)
            if items is None:
                raise ValueError(f"Failed to parse LLM response as JSON after retry: {retry_response.content[:200]}")
            response = retry_response

        # Apply overrides
        for item in items:
            if department:
                item.department = department
            if category:
                item.category = category
            if item.item_type not in VALID_ITEM_TYPES:
                item.item_type = "fact"

        # Save to DB
        await _save_items(client, company_id, user_id, session_id, items)
        await _update_session_status(client, session_id, "completed")

        return ExtractionResult(
            session_id=session_id,
            items=items,
            raw_llm_response=response.content,
            model_used=response.model_used,
            cost_yen=response.cost_yen,
        )

    except Exception as e:
        logger.error(f"Extraction failed for session {session_id}: {e}")
        await _update_session_status(client, session_id, "failed", str(e))
        raise


def _build_user_prompt(text: str, department: str | None, category: str | None) -> str:
    parts = [f"以下のテキストからナレッジを抽出してください:\n\n{text}"]
    if department:
        parts.append(f"\n部署: {department}")
    if category:
        parts.append(f"\nカテゴリ: {category}")
    return "".join(parts)


def _parse_items(content: str) -> list[ExtractedItem] | None:
    """Parse LLM response JSON into ExtractedItem list."""
    try:
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        if not isinstance(data, list):
            return None

        return [ExtractedItem(**item) for item in data]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"JSON parse error: {e}")
        return None


async def _create_session(client, company_id: str, user_id: str, text: str) -> UUID:
    result = client.table("knowledge_sessions").insert({
        "company_id": company_id,
        "user_id": user_id,
        "input_type": "text",
        "raw_content": text,
        "extraction_status": "processing",
    }).execute()
    return UUID(result.data[0]["id"])


async def _save_items(
    client, company_id: str, user_id: str, session_id: UUID, items: list[ExtractedItem]
) -> None:
    rows = [
        {
            "company_id": company_id,
            "session_id": str(session_id),
            "department": item.department,
            "category": item.category,
            "item_type": item.item_type,
            "title": item.title,
            "content": item.content,
            "conditions": item.conditions,
            "examples": item.examples,
            "exceptions": item.exceptions,
            "source_type": "explicit",
            "source_user_id": user_id,
            "confidence": item.confidence,
        }
        for item in items
    ]
    if rows:
        client.table("knowledge_items").insert(rows).execute()


async def _update_session_status(
    client, session_id: UUID, status: str, error: str | None = None
) -> None:
    update = {"extraction_status": status}
    if error:
        update["extraction_error"] = {"message": error}
    client.table("knowledge_sessions").update(update).eq("id", str(session_id)).execute()

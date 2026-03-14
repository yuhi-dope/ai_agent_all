"""Pydantic models for knowledge extraction."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ExtractedItem(BaseModel):
    """Single knowledge item extracted by LLM."""
    title: str
    content: str
    category: str
    item_type: str  # rule, flow, decision_logic, fact, tip
    department: str
    conditions: list[str] | None = None
    examples: list[str] | None = None
    exceptions: list[str] | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    """Result of a full extraction pipeline run."""
    session_id: UUID
    items: list[ExtractedItem]
    raw_llm_response: str
    model_used: str
    cost_yen: float

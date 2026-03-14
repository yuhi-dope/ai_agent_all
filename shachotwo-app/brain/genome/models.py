"""Pydantic models for genome industry templates."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class GenomeKnowledgeItem(BaseModel):
    """A single knowledge item within a template."""
    title: str
    content: str
    category: str
    item_type: str  # rule, flow, decision_logic, fact, tip
    department: str
    conditions: list[str] | None = None
    examples: list[str] | None = None
    exceptions: list[str] | None = None
    confidence: float = 0.7


class GenomeDepartment(BaseModel):
    """Department definition within a template."""
    name: str
    description: str
    items: list[GenomeKnowledgeItem]


class GenomeTemplate(BaseModel):
    """Industry template."""
    id: str
    name: str
    description: str
    industry: str
    sub_industries: list[str]
    typical_employee_range: str
    departments: list[GenomeDepartment]

    @property
    def total_items(self) -> int:
        return sum(len(d.items) for d in self.departments)


class TemplateApplicationResult(BaseModel):
    """Result of applying a template to a company."""
    template_id: str
    company_id: UUID
    items_created: int
    departments: list[str]

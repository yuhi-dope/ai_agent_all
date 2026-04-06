"""Pydantic models for genome industry templates."""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class TemplateVariable(BaseModel):
    """A variable placeholder definition for a genome template."""
    label: str
    default: str
    type: str = "text"  # "text" or "number"


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
    # BPO automation metadata
    bpo_automatable: bool = False
    bpo_method: str | None = None  # How to automate (SaaS name, RPA, etc.)
    # Scale trigger: employee count at which this item becomes mandatory
    scale_trigger: int | None = None  # e.g. 10, 50, 100, 300
    # Legal basis for mandatory items
    legal_basis: list[str] | None = None


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
    variables: dict[str, TemplateVariable] = {}
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

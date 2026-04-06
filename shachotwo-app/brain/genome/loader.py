"""Convenience wrapper for applying templates — used by the genome router."""
import logging

from brain.genome.applicator import apply_template as _apply_template
from brain.genome.models import TemplateApplicationResult

logger = logging.getLogger(__name__)


async def apply_template(
    company_id: str,
    template_name: str = "construction",
    include_common: bool = True,
    employee_count: int | None = None,
) -> dict:
    """Load template JSON and insert as knowledge_items for the company.

    Automatically includes the common (back-office) template unless disabled.

    Args:
        company_id: Target company UUID.
        template_name: Template ID (e.g. "construction", "manufacturing").
        include_common: If True, also apply common back-office template.
        employee_count: Company size — filters scale_trigger items (e.g. 50-person obligations).

    Returns:
        dict with template_id, company_id, items_created, departments.
    """
    result: TemplateApplicationResult = await _apply_template(
        template_id=template_name,
        company_id=company_id,
        include_common=include_common,
        employee_count=employee_count,
    )
    return {
        "template_id": result.template_id,
        "company_id": str(result.company_id),
        "items_created": result.items_created,
        "departments": result.departments,
    }

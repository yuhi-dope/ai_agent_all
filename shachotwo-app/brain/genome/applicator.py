"""Apply a genome template to a company — bulk insert knowledge_items + generate embeddings."""
import logging
import re
from uuid import UUID

from brain.genome.models import GenomeTemplate, TemplateApplicationResult
from brain.genome.templates import get_template
from brain.knowledge.embeddings import generate_embeddings
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

COMMON_TEMPLATE_ID = "common"


def _substitute_variables(text: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with values from the variables dict.

    Any placeholder that has no matching key is left as-is (not replaced with empty string).
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return re.sub(r'\{\{([a-z0-9_]+)\}\}', replacer, text)


def _substitute_row_variables(row: dict, variables: dict[str, str]) -> dict:
    """Substitute {{variable}} placeholders in all text fields of a knowledge item row."""
    for field in ("title", "content"):
        if row.get(field):
            row[field] = _substitute_variables(row[field], variables)
    for field in ("conditions", "examples", "exceptions"):
        if row.get(field) and isinstance(row[field], list):
            row[field] = [
                _substitute_variables(s, variables) if isinstance(s, str) else s
                for s in row[field]
            ]
    return row


def _build_variables(template: "GenomeTemplate", customizations: dict | None) -> dict[str, str]:
    """Build the final variables dict: template defaults overridden by user customizations."""
    # Start with template-defined defaults
    merged: dict[str, str] = {
        key: var.default
        for key, var in template.variables.items()
    }
    # Override with user-supplied values
    if customizations:
        user_vars = customizations.get("variables", {})
        if user_vars and isinstance(user_vars, dict):
            merged.update({k: str(v) for k, v in user_vars.items()})
    return merged


def _template_to_rows(
    template: GenomeTemplate,
    company_id: str,
    source_tag: str,
    dept_filter: set[str] | None = None,
    employee_count: int | None = None,
    variables: dict[str, str] | None = None,
) -> tuple[list[dict], set[str]]:
    """Convert template items to DB rows, filtering by department and scale_trigger.

    Variables substitution is applied to title, content, conditions, examples, exceptions.
    """
    rows: list[dict] = []
    departments_used: set[str] = set()
    effective_vars: dict[str, str] = variables or {}

    for dept in template.departments:
        if dept_filter and dept.name not in dept_filter:
            continue
        departments_used.add(dept.name)
        for item in dept.items:
            # Skip items that require a larger company size
            if item.scale_trigger and employee_count and employee_count < item.scale_trigger:
                continue
            row = {
                "company_id": company_id,
                "department": item.department,
                "category": item.category,
                "item_type": item.item_type,
                "title": item.title,
                "content": item.content,
                "conditions": item.conditions,
                "examples": item.examples,
                "exceptions": item.exceptions,
                "source_type": "template",
                "source_tag": source_tag,
                "confidence": item.confidence,
                "bpo_automatable": item.bpo_automatable,
                "bpo_method": item.bpo_method,
            }
            if effective_vars:
                row = _substitute_row_variables(row, effective_vars)
            rows.append(row)
    return rows, departments_used


async def apply_template(
    template_id: str,
    company_id: str,
    customizations: dict | None = None,
    include_common: bool = True,
    employee_count: int | None = None,
) -> TemplateApplicationResult:
    """Apply template to company: insert knowledge_items + generate embeddings + update company.

    Args:
        template_id: Industry template ID (e.g. "construction", "manufacturing").
        company_id: Target company UUID.
        customizations: Optional dict with "departments" filter etc.
        include_common: If True, also apply the common (back-office) template.
        employee_count: Company employee count — used to filter scale_trigger items.
    """
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"Template not found: {template_id}")

    dept_filter = None
    if customizations and "departments" in customizations:
        dept_filter = set(customizations["departments"])

    db = get_service_client()

    # 重複防止: テンプレート由来のアイテムを削除してから再適用
    existing = db.table("knowledge_items") \
        .select("id", count="exact") \
        .eq("company_id", company_id) \
        .eq("source_type", "template") \
        .execute()

    if existing.count and existing.count > 0:
        db.table("knowledge_items") \
            .delete() \
            .eq("company_id", company_id) \
            .eq("source_type", "template") \
            .execute()
        logger.info(f"Deleted {existing.count} existing template items for company {company_id}")

    all_rows: list[dict] = []
    all_departments: set[str] = set()

    # Build merged variables dict (used for both common and industry template substitution)
    # Priority (lowest to highest): common defaults < industry defaults < user customizations
    merged_variables: dict[str, str] = {}

    common_template: GenomeTemplate | None = None
    if include_common and template_id != COMMON_TEMPLATE_ID:
        common_template = get_template(COMMON_TEMPLATE_ID)
        if common_template:
            # Layer 1: common template defaults
            merged_variables.update({k: v.default for k, v in common_template.variables.items()})

    # Layer 2: industry template defaults (override common defaults for same keys)
    merged_variables.update({k: v.default for k, v in template.variables.items()})

    # Layer 3: user-supplied values (highest priority)
    if customizations:
        user_vars = customizations.get("variables", {})
        if user_vars and isinstance(user_vars, dict):
            merged_variables.update({k: str(v) for k, v in user_vars.items()})

    # 1) Common template (back-office: 経理・総務・法務・情報システム・営業事務)
    if common_template:
        common_rows, common_depts = _template_to_rows(
            common_template, company_id, source_tag="common",
            employee_count=employee_count,
            variables=merged_variables,
        )
        all_rows.extend(common_rows)
        all_departments.update(common_depts)
        logger.info(f"Common template: {len(common_rows)} items from {sorted(common_depts)}")
    elif include_common and template_id != COMMON_TEMPLATE_ID:
        logger.warning("Common template not found — skipping back-office items")

    # 2) Industry-specific template
    industry_rows, industry_depts = _template_to_rows(
        template, company_id, source_tag=template_id,
        dept_filter=dept_filter, employee_count=employee_count,
        variables=merged_variables,
    )
    all_rows.extend(industry_rows)
    all_departments.update(industry_depts)

    if not all_rows:
        return TemplateApplicationResult(
            template_id=template_id,
            company_id=UUID(company_id),
            items_created=0,
            departments=[],
        )

    # Generate embeddings for all items
    texts = [f"{r['title']}\n{r['content']}" for r in all_rows]
    try:
        embeddings = await generate_embeddings(texts)
        for row, emb in zip(all_rows, embeddings):
            row["embedding"] = emb
        logger.info(f"Generated {len(embeddings)} embeddings")
    except Exception as e:
        logger.warning(f"Embedding generation failed, inserting without embeddings: {e}")

    # Batch insert (Supabase handles up to 1000 rows)
    db.table("knowledge_items").insert(all_rows).execute()

    # Store template info
    applied_templates = [template_id]
    if include_common and template_id != COMMON_TEMPLATE_ID:
        applied_templates.insert(0, COMMON_TEMPLATE_ID)

    db.table("companies").update({
        "genome_customizations": {
            **(customizations or {}),
            "applied_templates": applied_templates,
            "employee_count": employee_count,
        },
    }).eq("id", company_id).execute()

    dept_list = sorted(all_departments)
    logger.info(
        f"Applied templates {applied_templates} to company {company_id}: "
        f"{len(all_rows)} items, {dept_list}"
    )

    return TemplateApplicationResult(
        template_id=template_id,
        company_id=UUID(company_id),
        items_created=len(all_rows),
        departments=dept_list,
    )

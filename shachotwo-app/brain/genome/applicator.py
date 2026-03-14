"""Apply a genome template to a company — bulk insert knowledge_items + generate embeddings."""
import logging
from uuid import UUID

from brain.genome.models import TemplateApplicationResult
from brain.genome.templates import get_template
from brain.knowledge.embeddings import generate_embeddings
from db.supabase import get_service_client

logger = logging.getLogger(__name__)


async def apply_template(
    template_id: str,
    company_id: str,
    customizations: dict | None = None,
) -> TemplateApplicationResult:
    """Apply template to company: insert knowledge_items + generate embeddings + update company."""
    template = get_template(template_id)
    if template is None:
        raise ValueError(f"Template not found: {template_id}")

    # Filter departments if customizations specify
    dept_filter = None
    if customizations and "departments" in customizations:
        dept_filter = set(customizations["departments"])

    db = get_service_client()

    # 重複防止: 同テンプレート由来のアイテムが既にあれば削除してから再適用
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

    rows = []
    departments_used = set()

    for dept in template.departments:
        if dept_filter and dept.name not in dept_filter:
            continue
        departments_used.add(dept.name)
        for item in dept.items:
            rows.append({
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
                "confidence": item.confidence,
            })

    if not rows:
        return TemplateApplicationResult(
            template_id=template_id,
            company_id=UUID(company_id),
            items_created=0,
            departments=[],
        )

    # Generate embeddings for all items
    texts = [f"{r['title']}\n{r['content']}" for r in rows]
    try:
        embeddings = await generate_embeddings(texts)
        for row, emb in zip(rows, embeddings):
            row["embedding"] = emb
        logger.info(f"Generated {len(embeddings)} embeddings for template {template_id}")
    except Exception as e:
        logger.warning(f"Embedding generation failed, inserting without embeddings: {e}")
        # Continue without embeddings — they can be backfilled later

    # Batch insert (Supabase handles up to 1000 rows)
    db.table("knowledge_items").insert(rows).execute()

    # Store template info as customizations (genome_template_id is UUID — no FK table in MVP)
    db.table("companies").update({
        "genome_customizations": {**(customizations or {}), "applied_template": template_id},
    }).eq("id", company_id).execute()

    dept_list = sorted(departments_used)
    logger.info(f"Applied template {template_id} to company {company_id}: {len(rows)} items, {dept_list}")

    return TemplateApplicationResult(
        template_id=template_id,
        company_id=UUID(company_id),
        items_created=len(rows),
        departments=dept_list,
    )

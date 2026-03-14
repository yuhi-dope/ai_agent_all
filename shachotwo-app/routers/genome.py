"""Genome template endpoints — apply industry templates to populate knowledge_items."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from auth.middleware import require_role
from auth.jwt import JWTClaims
from brain.genome.loader import apply_template
from brain.genome.templates import list_templates

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ApplyTemplateRequest(BaseModel):
    template_id: str = "construction"
    departments: Optional[list[str]] = None


class ApplyTemplateResponse(BaseModel):
    template_id: str
    company_id: str
    items_created: int
    departments: list[str]
    message: str


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str
    industry: str
    total_items: int
    departments: list[str]


class TemplateListResponse(BaseModel):
    templates: list[TemplateSummary]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/genome/templates", response_model=TemplateListResponse)
async def list_available_templates(
    user: JWTClaims = Depends(require_role("admin", "editor")),
):
    """利用可能な業種テンプレート一覧を取得"""
    templates = list_templates()
    return TemplateListResponse(
        templates=[
            TemplateSummary(
                id=t.id,
                name=t.name,
                description=t.description,
                industry=t.industry,
                total_items=t.total_items,
                departments=[d.name for d in t.departments],
            )
            for t in templates
        ]
    )


@router.post("/genome/apply-template", response_model=ApplyTemplateResponse)
async def apply_template_endpoint(
    body: ApplyTemplateRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """業種テンプレートを適用し、ナレッジアイテムを一括登録する。

    admin ロール必須。テンプレートのナレッジアイテムが knowledge_items に挿入され、
    embedding も自動生成される。
    """
    try:
        result = await apply_template(
            company_id=user.company_id,
            template_name=body.template_id,
        )
        return ApplyTemplateResponse(
            template_id=result["template_id"],
            company_id=result["company_id"],
            items_created=result["items_created"],
            departments=result["departments"],
            message=f"{result['items_created']}件のナレッジが追加されました",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "TEMPLATE_NOT_FOUND", "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"Template application failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "TEMPLATE_ERROR", "message": f"テンプレート適用に失敗しました: {e}"}},
        )

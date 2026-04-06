"""Knowledge Graph エンドポイント（エンティティ・関係）。"""
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.jwt import JWTClaims
from auth.middleware import get_current_user
from brain.knowledge.entity_extractor import EntityExtractor
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────
# Pydantic モデル
# ─────────────────────────────────────

class EntityResponse(BaseModel):
    id: UUID
    company_id: UUID
    entity_type: str
    entity_key: str
    display_name: str
    properties: dict[str, Any]
    source_connector: Optional[str] = None
    created_at: str


class RelationResponse(BaseModel):
    id: UUID
    company_id: UUID
    from_entity_id: UUID
    relation_type: str
    to_entity_id: UUID
    properties: dict[str, Any]
    confidence_score: float
    source: str
    created_at: str


class RelatedEntityResponse(BaseModel):
    entity: EntityResponse
    relation: RelationResponse
    direction: str  # "outbound" | "inbound"


class EntityWithRelations(BaseModel):
    entity: EntityResponse
    related: list[RelatedEntityResponse]


class EntityListResponse(BaseModel):
    items: list[EntityResponse]
    total: int
    has_more: bool


class ExtractRequest(BaseModel):
    text: str
    source_connector: str = "manual"
    infer_relations: bool = True


class ExtractResponse(BaseModel):
    entity_ids: list[str]
    entity_count: int
    relation_count: int


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.get("/kg/entities", response_model=EntityListResponse)
async def list_entities(
    entity_type: Optional[str] = Query(None, description="エンティティ型フィルタ"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
) -> EntityListResponse:
    """エンティティ一覧を返す（company_id フィルタ・entity_type フィルタ）。"""
    db = get_service_client()

    q = db.table("kg_entities").select(
        "id, company_id, entity_type, entity_key, display_name, properties, source_connector, created_at",
        count="exact",
    ).eq("company_id", str(user.company_id)).order("created_at", desc=True).range(offset, offset + limit - 1)

    if entity_type:
        q = q.eq("entity_type", entity_type)

    result = q.execute()

    items = [EntityResponse(**row) for row in (result.data or [])]
    total = result.count or 0

    return EntityListResponse(
        items=items,
        total=total,
        has_more=(offset + limit) < total,
    )


@router.get("/kg/entities/{entity_id}/related", response_model=EntityWithRelations)
async def get_related_entities(
    entity_id: UUID,
    hops: int = Query(1, ge=1, le=2, description="ホップ数（最大2）"),
    user: JWTClaims = Depends(get_current_user),
) -> EntityWithRelations:
    """指定エンティティに関連するエンティティと relation を返す（最大2ホップ）。"""
    db = get_service_client()

    # 起点エンティティ取得（company_id フィルタ）
    root_result = db.table("kg_entities").select(
        "id, company_id, entity_type, entity_key, display_name, properties, source_connector, created_at"
    ).eq("id", str(entity_id)).eq("company_id", str(user.company_id)).single().execute()

    if not root_result.data:
        raise HTTPException(status_code=404, detail="Entity not found")

    root_entity = EntityResponse(**root_result.data)

    # 1ホップ目の関係を収集
    visited_ids: set[str] = {str(entity_id)}
    related_items: list[RelatedEntityResponse] = []

    await _collect_relations(db, str(entity_id), str(user.company_id), visited_ids, related_items)

    # 2ホップ目
    if hops >= 2:
        hop1_ids = [r.entity.id for r in related_items]
        for hop1_id in hop1_ids:
            await _collect_relations(db, str(hop1_id), str(user.company_id), visited_ids, related_items)

    return EntityWithRelations(entity=root_entity, related=related_items)


async def _collect_relations(
    db: Any,
    entity_id: str,
    company_id: str,
    visited_ids: set[str],
    result_list: list[RelatedEntityResponse],
) -> None:
    """指定エンティティの outbound/inbound 関係を収集してresult_listに追記する。"""

    # Outbound（from_entity_id = entity_id）
    out_rels = db.table("kg_relations").select(
        "id, company_id, from_entity_id, relation_type, to_entity_id, properties, confidence_score, source, created_at"
    ).eq("company_id", company_id).eq("from_entity_id", entity_id).execute()

    for rel_row in (out_rels.data or []):
        to_id = str(rel_row["to_entity_id"])
        if to_id in visited_ids:
            continue
        entity_result = db.table("kg_entities").select(
            "id, company_id, entity_type, entity_key, display_name, properties, source_connector, created_at"
        ).eq("id", to_id).eq("company_id", company_id).single().execute()
        if not entity_result.data:
            continue
        visited_ids.add(to_id)
        result_list.append(RelatedEntityResponse(
            entity=EntityResponse(**entity_result.data),
            relation=RelationResponse(**rel_row),
            direction="outbound",
        ))

    # Inbound（to_entity_id = entity_id）
    in_rels = db.table("kg_relations").select(
        "id, company_id, from_entity_id, relation_type, to_entity_id, properties, confidence_score, source, created_at"
    ).eq("company_id", company_id).eq("to_entity_id", entity_id).execute()

    for rel_row in (in_rels.data or []):
        from_id = str(rel_row["from_entity_id"])
        if from_id in visited_ids:
            continue
        entity_result = db.table("kg_entities").select(
            "id, company_id, entity_type, entity_key, display_name, properties, source_connector, created_at"
        ).eq("id", from_id).eq("company_id", company_id).single().execute()
        if not entity_result.data:
            continue
        visited_ids.add(from_id)
        result_list.append(RelatedEntityResponse(
            entity=EntityResponse(**entity_result.data),
            relation=RelationResponse(**rel_row),
            direction="inbound",
        ))


@router.post("/kg/extract", response_model=ExtractResponse)
async def extract_entities_from_text(
    body: ExtractRequest,
    user: JWTClaims = Depends(get_current_user),
) -> ExtractResponse:
    """テキストからエンティティを抽出して kg_entities に保存する。
    infer_relations=True の場合は関係推論も実行する。
    """
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=422, detail="text is required")

    extractor = EntityExtractor()
    company_id = str(user.company_id)

    try:
        entities = await extractor.extract_from_text(
            text=body.text,
            company_id=company_id,
            source_connector=body.source_connector,
        )
    except Exception as exc:
        logger.error("kg/extract: entity extraction failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        entity_ids = await extractor.upsert_entities(entities, company_id)
    except Exception as exc:
        logger.error("kg/extract: entity upsert failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    relation_count = 0
    if body.infer_relations and len(entity_ids) >= 2:
        try:
            relations = await extractor.infer_relations(
                entity_ids=entity_ids,
                source_text=body.text,
                company_id=company_id,
            )
            relation_count = len(relations)
        except Exception as exc:
            logger.warning("kg/extract: relation inference failed (non-fatal): %s", exc)

    return ExtractResponse(
        entity_ids=entity_ids,
        entity_count=len(entity_ids),
        relation_count=relation_count,
    )

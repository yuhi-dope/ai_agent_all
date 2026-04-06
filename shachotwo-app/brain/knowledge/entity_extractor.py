"""KnowledgeGraph エンティティ抽出・UPSERT・関係推論モジュール。"""
import json
import logging
from dataclasses import dataclass, field

from db.supabase import get_service_client
from llm.client import LLMTask, get_llm_client
from shared.enums import ModelTier

logger = logging.getLogger(__name__)

# 許可されたエンティティ型
VALID_ENTITY_TYPES = frozenset({
    "Company", "Person", "Project", "Contract",
    "Product", "Transaction", "Document", "Task",
})

# 許可された関係型
VALID_RELATION_TYPES = frozenset({
    "BELONGS_TO", "OWNS", "RELATED_TO", "SUPPLIED_BY",
    "EXECUTED_BY", "DERIVED_FROM", "DEPENDS_ON",
})

_NER_SYSTEM_PROMPT = """\
あなたは日本語テキストから固有表現を抽出する専門家です。
以下のエンティティ型を抽出してください:
- Company: 企業名・組織名
- Person: 人名
- Project: プロジェクト名・案件名
- Contract: 契約名・契約番号
- Product: 製品名・サービス名
- Transaction: 取引名・取引番号・金額
- Document: 文書名・書類名
- Task: タスク名・作業名

必ずJSON配列のみを返してください（説明文不要）。
フォーマット:
[
  {
    "entity_type": "Company",
    "display_name": "株式会社サンプル",
    "properties": {"industry": "製造業"}
  }
]
"""

_RELATION_SYSTEM_PROMPT = """\
あなたは日本語テキストからエンティティ間の関係を推論する専門家です。
以下の関係型のみを使用してください:
- BELONGS_TO: AはBに所属する
- OWNS: AはBを所有する
- RELATED_TO: AはBに関連する
- SUPPLIED_BY: AはBから供給される
- EXECUTED_BY: AはBによって実行される
- DERIVED_FROM: AはBから派生する
- DEPENDS_ON: AはBに依存する

必ずJSON配列のみを返してください（説明文不要）。
フォーマット:
[
  {
    "from_display_name": "プロジェクトA",
    "relation_type": "EXECUTED_BY",
    "to_display_name": "田中太郎",
    "confidence_score": 0.9,
    "properties": {}
  }
]
"""


@dataclass
class ExtractedEntity:
    """LLMが抽出したエンティティ。"""
    entity_type: str
    display_name: str
    entity_key: str           # "{source_connector}:{display_name}" でユニークキー生成
    properties: dict = field(default_factory=dict)
    source_connector: str = "manual"


@dataclass
class KGRelation:
    """Knowledge Graph の関係。"""
    from_entity_id: str
    relation_type: str
    to_entity_id: str
    company_id: str
    confidence_score: float = 1.0
    properties: dict = field(default_factory=dict)
    source: str = "auto_extracted"


class EntityExtractor:
    """テキストからエンティティを抽出し kg_entities / kg_relations に保存するクラス。"""

    def __init__(self) -> None:
        self._llm = get_llm_client()

    async def extract_from_text(
        self,
        text: str,
        company_id: str,
        source_connector: str = "manual",
    ) -> list[ExtractedEntity]:
        """LLMでNER（固有表現抽出）を実行してエンティティリストを返す。

        Args:
            text: 抽出対象のテキスト
            company_id: 対象企業ID（ログ・コスト追跡用）
            source_connector: どのコネクタ由来か（デフォルト: "manual"）

        Returns:
            抽出されたエンティティのリスト
        """
        if not text or not text.strip():
            return []

        response = await self._llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _NER_SYSTEM_PROMPT},
                {"role": "user", "content": f"以下のテキストからエンティティを抽出してください:\n\n{text}"},
            ],
            tier=ModelTier.FAST,
            task_type="entity_extraction",
            company_id=company_id,
            max_tokens=2048,
            temperature=0.1,
        ))

        entities: list[ExtractedEntity] = []
        try:
            raw = response.content.strip()
            # コードブロックを除去
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            items = json.loads(raw)
            for item in items:
                entity_type = item.get("entity_type", "")
                display_name = item.get("display_name", "").strip()
                if entity_type not in VALID_ENTITY_TYPES or not display_name:
                    continue
                entity_key = f"{source_connector}:{display_name}"
                entities.append(ExtractedEntity(
                    entity_type=entity_type,
                    display_name=display_name,
                    entity_key=entity_key,
                    properties=item.get("properties", {}),
                    source_connector=source_connector,
                ))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("EntityExtractor: NER JSONパース失敗: %s", exc)

        logger.info(
            "EntityExtractor: %d entities extracted from text (company=%s)",
            len(entities), company_id,
        )
        return entities

    async def upsert_entities(
        self,
        entities: list[ExtractedEntity],
        company_id: str,
    ) -> list[str]:
        """kg_entities にUPSERT（entity_key で重複チェック）してIDリストを返す。

        Args:
            entities: 保存するエンティティのリスト
            company_id: 対象企業ID

        Returns:
            保存されたエンティティのUUID文字列リスト
        """
        if not entities:
            return []

        db = get_service_client()
        entity_ids: list[str] = []

        for entity in entities:
            payload = {
                "company_id": company_id,
                "entity_type": entity.entity_type,
                "entity_key": entity.entity_key,
                "display_name": entity.display_name,
                "properties": entity.properties,
                "source_connector": entity.source_connector,
            }
            try:
                result = db.table("kg_entities").upsert(
                    payload,
                    on_conflict="company_id,entity_key",
                ).execute()
                if result.data:
                    entity_ids.append(result.data[0]["id"])
            except Exception as exc:
                logger.error(
                    "EntityExtractor: upsert failed for entity_key=%s: %s",
                    entity.entity_key, exc,
                )

        logger.info(
            "EntityExtractor: upserted %d/%d entities (company=%s)",
            len(entity_ids), len(entities), company_id,
        )
        return entity_ids

    async def infer_relations(
        self,
        entity_ids: list[str],
        source_text: str,
        company_id: str,
    ) -> list[KGRelation]:
        """エンティティ間の関係をLLMで推論して kg_relations に保存する。

        Args:
            entity_ids: 対象エンティティのUUIDリスト
            source_text: 関係推論の根拠テキスト
            company_id: 対象企業ID

        Returns:
            保存されたKGRelationのリスト
        """
        if len(entity_ids) < 2:
            return []

        db = get_service_client()

        # エンティティ情報を取得
        result = db.table("kg_entities").select(
            "id, display_name, entity_type"
        ).in_("id", entity_ids).eq("company_id", company_id).execute()

        if not result.data or len(result.data) < 2:
            return []

        entities_summary = "\n".join(
            f"- {e['entity_type']}: {e['display_name']} (id={e['id']})"
            for e in result.data
        )
        id_by_name = {e["display_name"]: e["id"] for e in result.data}

        response = await self._llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _RELATION_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"以下のエンティティ一覧とテキストを参考に、エンティティ間の関係を推論してください。\n\n"
                    f"エンティティ一覧:\n{entities_summary}\n\n"
                    f"テキスト:\n{source_text}"
                )},
            ],
            tier=ModelTier.FAST,
            task_type="relation_inference",
            company_id=company_id,
            max_tokens=2048,
            temperature=0.1,
        ))

        relations: list[KGRelation] = []
        try:
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            items = json.loads(raw)
            for item in items:
                relation_type = item.get("relation_type", "")
                from_name = item.get("from_display_name", "")
                to_name = item.get("to_display_name", "")
                if relation_type not in VALID_RELATION_TYPES:
                    continue
                from_id = id_by_name.get(from_name)
                to_id = id_by_name.get(to_name)
                if not from_id or not to_id or from_id == to_id:
                    continue
                confidence = float(item.get("confidence_score", 1.0))

                relation_payload = {
                    "company_id": company_id,
                    "from_entity_id": from_id,
                    "relation_type": relation_type,
                    "to_entity_id": to_id,
                    "properties": item.get("properties", {}),
                    "confidence_score": confidence,
                    "source": "auto_extracted",
                }
                try:
                    rel_result = db.table("kg_relations").insert(relation_payload).execute()
                    if rel_result.data:
                        relations.append(KGRelation(
                            from_entity_id=from_id,
                            relation_type=relation_type,
                            to_entity_id=to_id,
                            company_id=company_id,
                            confidence_score=confidence,
                            properties=item.get("properties", {}),
                        ))
                except Exception as exc:
                    logger.error(
                        "EntityExtractor: relation insert failed (%s->%s): %s",
                        from_id, to_id, exc,
                    )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("EntityExtractor: relation JSONパース失敗: %s", exc)

        logger.info(
            "EntityExtractor: inferred %d relations (company=%s)",
            len(relations), company_id,
        )
        return relations

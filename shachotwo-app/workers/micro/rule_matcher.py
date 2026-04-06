"""rule_matcher マイクロエージェント。knowledge_itemsからルールを照合して適用する。"""
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError
from db.supabase import get_service_client

logger = logging.getLogger(__name__)


async def run_rule_matcher(input: MicroAgentInput) -> MicroAgentOutput:
    """
    knowledge_items テーブルからdomainに一致するルールを検索し、
    extracted_data に適用して単価・基準値を補完する。

    payload:
        extracted_data (dict): 構造化済みデータ（structured_extractorの出力）
        domain (str): 照合ドメイン（例: "construction_estimation"）
        category (str, optional): カテゴリフィルタ（例: "unit_price"）

    result:
        matched_rules (list[dict]): マッチしたルール一覧
        applied_values (dict): ルールで補完・更新された値
        unmatched_fields (list[str]): ルールが見つからなかったフィールド
    """
    start_ms = int(time.time() * 1000)
    agent_name = "rule_matcher"

    try:
        extracted_data: dict[str, Any] = input.payload.get("extracted_data", {})
        domain: str = input.payload.get("domain", "")
        category: str | None = input.payload.get("category")

        if not domain:
            raise MicroAgentError(agent_name, "input_validation", "domain が必要です")

        db = get_service_client()

        # knowledge_items から該当ドメインのルールを取得
        query = (
            db.table("knowledge_items")
            .select("id, title, content, category, tags, confidence_score")
            .eq("company_id", input.company_id)
            .eq("domain", domain)
            .eq("is_active", True)
            .order("confidence_score", desc=True)
            .limit(50)
        )
        if category:
            query = query.eq("category", category)

        response = query.execute()
        rules: list[dict] = response.data or []

        if not rules:
            # ルールなし → データそのまま返す（失敗ではない）
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name,
                success=True,
                result={
                    "matched_rules": [],
                    "applied_values": extracted_data,
                    "unmatched_fields": list(extracted_data.keys()),
                },
                confidence=0.5,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        # ルール照合: タイトル・タグをキーワードとしてextracted_dataのフィールドと照合
        matched_rules: list[dict] = []
        applied_values: dict[str, Any] = dict(extracted_data)
        matched_fields: set[str] = set()

        for rule in rules:
            rule_tags: list[str] = rule.get("tags") or []
            rule_title: str = rule.get("title", "").lower()

            for field_key, field_value in extracted_data.items():
                field_lower = field_key.lower()
                # フィールド名がルールのタグまたはタイトルに含まれるかチェック
                if any(tag.lower() in field_lower or field_lower in tag.lower() for tag in rule_tags) \
                        or field_lower in rule_title:
                    matched_rules.append({
                        "rule_id": rule["id"],
                        "title": rule["title"],
                        "field": field_key,
                        "confidence": rule.get("confidence_score", 0.8),
                    })
                    matched_fields.add(field_key)
                    # contentにJSONが含まれる場合は値を上書き
                    try:
                        import json
                        rule_data = json.loads(rule["content"])
                        if field_key in rule_data:
                            applied_values[field_key] = rule_data[field_key]
                    except (json.JSONDecodeError, TypeError):
                        pass

        unmatched_fields = [k for k in extracted_data if k not in matched_fields]
        confidence = len(matched_fields) / len(extracted_data) if extracted_data else 0.5

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "matched_rules": matched_rules,
                "applied_values": applied_values,
                "unmatched_fields": unmatched_fields,
            },
            confidence=round(confidence, 3),
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"rule_matcher error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

"""structured_extractor マイクロエージェント。テキストをスキーマ指定でJSON構造化する。"""
import json
import time
import logging
import re
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError
from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """あなたはテキストから構造化データを抽出する専門家です。
与えられたJSONスキーマに従って、テキストから情報を抽出してください。

ルール:
- スキーマのフィールドが見つからない場合は null を使う
- 数値は数値型（文字列ではなく）で返す
- 日付は YYYY-MM-DD 形式で返す
- 必ずJSONのみを返す（説明文不要）
"""


async def run_structured_extractor(input: MicroAgentInput) -> MicroAgentOutput:
    """
    テキストをLLMでスキーマに沿ったJSONに構造化する。

    payload:
        text (str): 抽出元テキスト
        schema (dict): 抽出スキーマ（フィールド名: 説明）
        domain (str, optional): ドメインヒント（例: "construction_estimation"）

    result:
        extracted (dict): 抽出されたデータ
        missing_fields (list[str]): 見つからなかったフィールド
    """
    start_ms = int(time.time() * 1000)
    agent_name = "structured_extractor"
    llm = get_llm_client()

    try:
        text = input.payload.get("text", "")
        schema = input.payload.get("schema", {})
        domain = input.payload.get("domain", "general")

        if not text:
            raise MicroAgentError(agent_name, "input_validation", "text が空です")
        if not schema:
            raise MicroAgentError(agent_name, "input_validation", "schema が空です")

        schema_desc = json.dumps(schema, ensure_ascii=False, indent=2)
        user_prompt = f"""ドメイン: {domain}

抽出スキーマ:
{schema_desc}

テキスト:
{text[:4000]}

上記スキーマに従ってテキストから情報を抽出し、JSONのみを返してください。"""

        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.STANDARD,
            task_type="structured_extractor",
            company_id=input.company_id,
            temperature=0.0,
        ))

        # JSON抽出
        raw = response.content.strip()
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            raise MicroAgentError(agent_name, "parse", f"LLMがJSONを返しませんでした: {raw[:200]}")

        extracted: dict[str, Any] = json.loads(json_match.group())

        # missing_fields 計算
        missing_fields = [k for k, v in extracted.items() if v is None]
        present_count = len(schema) - len(missing_fields)
        confidence = present_count / len(schema) if schema else 1.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={"extracted": extracted, "missing_fields": missing_fields},
            confidence=round(confidence, 3),
            cost_yen=response.cost_yen,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except json.JSONDecodeError as e:
        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": f"JSON parse error: {e}"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"structured_extractor error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

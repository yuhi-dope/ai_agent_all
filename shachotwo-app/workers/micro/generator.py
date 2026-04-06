"""document_generator マイクロエージェント。テンプレート名+データからドキュメントを生成する。"""
import json
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

_TEMPLATE_PROMPTS: dict[str, str] = {
    "estimation_cover": "建設工事の見積書表紙を作成してください。正式な日本語書式で。",
    "safety_roster": "建設現場の安全書類（作業員名簿）を作成してください。",
    "monthly_report": "月次報告書を作成してください。要点を箇条書きで整理してください。",
    "invoice": "請求書を作成してください。正式な日本語書式で。",
    "approval_request": "承認依頼文書を作成してください。簡潔に要点を伝えてください。",
    "summary": "以下のデータを読みやすい要約にまとめてください。",
}

_FORMAT_INSTRUCTIONS: dict[str, str] = {
    "text": "プレーンテキストで出力してください。",
    "markdown": "Markdown形式で出力してください。",
    "json": "JSONのみを出力してください（説明文不要）。",
}


async def run_document_generator(input: MicroAgentInput) -> MicroAgentOutput:
    """
    テンプレート名とデータからドキュメントを生成する。

    payload:
        template_name (str): テンプレート識別子
        data (dict): 埋め込むデータ
        format (str): "text" | "markdown" | "json"

    result:
        content (str): 生成されたドキュメント
        format (str): 出力フォーマット
        char_count (int): 文字数
    """
    start_ms = int(time.time() * 1000)
    agent_name = "document_generator"
    llm = get_llm_client()

    try:
        template_name: str = input.payload.get("template_name", "summary")
        data: dict[str, Any] = input.payload.get("data", {})
        fmt: str = input.payload.get("format", "text")

        template_prompt = _TEMPLATE_PROMPTS.get(template_name, _TEMPLATE_PROMPTS["summary"])
        format_instruction = _FORMAT_INSTRUCTIONS.get(fmt, _FORMAT_INSTRUCTIONS["text"])

        data_str = json.dumps(data, ensure_ascii=False, indent=2)
        user_prompt = f"""{template_prompt}
{format_instruction}

入力データ:
{data_str[:3000]}"""

        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": "あなたは日本のビジネス文書作成の専門家です。与えられたデータから適切な文書を作成します。"},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.STANDARD,
            task_type="document_generator",
            company_id=input.company_id,
            temperature=0.3,
        ))

        content = response.content.strip()
        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"content": content, "format": fmt, "char_count": len(content)},
            confidence=0.9, cost_yen=response.cost_yen, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"document_generator error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

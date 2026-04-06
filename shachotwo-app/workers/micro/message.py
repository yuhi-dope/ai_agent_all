"""共通マイクロエージェント: メッセージ・文書ドラフト生成"""
import logging
from dataclasses import dataclass

from llm.client import LLMTask, LLMResponse, ModelTier, get_llm_client

logger = logging.getLogger(__name__)


@dataclass
class MessageDraftResult:
    """メッセージドラフト生成結果"""
    subject: str
    body: str
    document_type: str
    model_used: str | None = None
    is_template_fallback: bool = False


async def run_message_drafter(
    document_type: str,
    context: dict,
    company_id: str | None = None,
    model_tier: ModelTier = ModelTier.FAST,
) -> MessageDraftResult:
    """
    督促状・催告書・通知書等の文書ドラフトを生成する共通マイクロエージェント。

    Args:
        document_type: 文書種別（例: "督促状（初回）", "催告書（2回目）", "内容証明郵便", "法的措置予告"）
        context: ドラフト生成に必要なコンテキスト情報
        company_id: テナントID（コスト追跡用）
        model_tier: 使用するLLMのティア

    Returns:
        MessageDraftResult: 生成された文書のドラフト
    """
    llm = get_llm_client()

    system_prompt = (
        "あなたは不動産管理会社の法務・業務担当者です。"
        "入居者への督促・催告文書を、法的に適切かつ丁寧な文体で作成してください。"
        "文書は件名と本文の2パートで構成し、以下のJSON形式で返してください:\n"
        '{"subject": "件名", "body": "本文全文"}'
    )

    user_prompt = f"""
以下の情報をもとに「{document_type}」を作成してください。

物件名: {context.get('property_name', '')}
入居者名: {context.get('tenant_name', '')}
部屋番号: {context.get('room_number', '')}
月額家賃: {context.get('monthly_rent', 0):,}円
支払期日: {context.get('payment_due_date', '')}
滞納日数: {context.get('overdue_days', 0)}日
滞納損害金: {context.get('late_fee', 0):,}円
合計未払額: {context.get('total_overdue', 0):,}円
参照日: {context.get('reference_date', '')}
"""

    try:
        task = LLMTask(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tier=model_tier,
            max_tokens=1024,
            temperature=0.3,
            company_id=company_id,
            task_type="message_draft",
        )
        response: LLMResponse = await llm.generate(task)

        import json
        import re
        content = response.content.strip()
        # コードフェンスを除去
        content = re.sub(r"^```(?:json)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)

        parsed = json.loads(content)
        return MessageDraftResult(
            subject=parsed.get("subject", f"【{document_type}】家賃滞納について"),
            body=parsed.get("body", ""),
            document_type=document_type,
            model_used=response.model_used,
            is_template_fallback=False,
        )

    except Exception as e:
        logger.warning(f"run_message_drafter LLM失敗、テンプレートにフォールバック: {e}")
        return _template_fallback(document_type, context)


def _template_fallback(document_type: str, context: dict) -> MessageDraftResult:
    """LLM失敗時のテンプレートフォールバック"""
    tenant_name = context.get("tenant_name", "入居者")
    property_name = context.get("property_name", "物件")
    room_number = context.get("room_number", "")
    monthly_rent = context.get("monthly_rent", 0)
    overdue_days = context.get("overdue_days", 0)
    total_overdue = context.get("total_overdue", 0)
    payment_due_date = context.get("payment_due_date", "")
    reference_date = context.get("reference_date", "")

    subject = f"【{document_type}】家賃滞納のご通知 — {property_name} {room_number}号室"

    body_lines = [
        f"{tenant_name} 様",
        "",
        f"平素より {property_name} をご利用いただき、誠にありがとうございます。",
        "",
        f"さて、{payment_due_date} を支払期日とする家賃（月額 {monthly_rent:,}円）が、"
        f"{reference_date} 現在、{overdue_days}日間未納となっております。",
        "",
        f"未払金額合計: {total_overdue:,}円",
        "",
    ]

    if document_type in ("督促状（初回）",):
        body_lines += [
            "早急にご入金いただきますようお願い申し上げます。",
            "すでにお振込み済みの場合はご連絡ください。",
        ]
    elif document_type in ("催告書（2回目）",):
        body_lines += [
            "本書到達後7日以内にお支払いいただけない場合、",
            "法的措置を検討せざるを得ない場合がございます。",
        ]
    elif document_type in ("内容証明郵便",):
        body_lines += [
            "本書は内容証明郵便にてお送りしております。",
            "本書到達後7日以内に未払金額全額をお支払いください。",
            "期日までにお支払いいただけない場合、賃貸借契約の解除を行います。",
        ]
    elif document_type in ("法的措置予告",):
        body_lines += [
            "すでに複数回のご連絡を差し上げておりますが、ご入金が確認できておりません。",
            "本書到達後3日以内にお支払いいただけない場合、",
            "建物明渡し請求等の法的措置を行います。",
        ]

    body_lines += ["", "ご不明な点は下記までご連絡ください。", "", "管理部"]

    return MessageDraftResult(
        subject=subject,
        body="\n".join(body_lines),
        document_type=document_type,
        model_used=None,
        is_template_fallback=True,
    )

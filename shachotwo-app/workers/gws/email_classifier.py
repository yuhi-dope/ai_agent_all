"""受信メール自動分類器。LLMベースでメールをカテゴリ分けしてevent_typeにマッピングする。

分類カテゴリ → event_type マッピング:
  reply_interested    → email_reply_interested    (アウトリーチ返信・興味あり)
  reply_not_interested → (スキップ)
  inquiry_new          → email_inquiry_new         (新規問い合わせ)
  meeting_request      → email_meeting_request     (商談/面談リクエスト)
  support_request      → email_support_request     (サポート問い合わせ)
  payment_notification → email_payment_received    (入金/請求関連)
  internal             → (スキップ)
  spam_or_newsletter   → (スキップ)
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# カテゴリ → event_type マッピング（スキップ対象はNone）
CATEGORY_TO_EVENT: dict[str, str | None] = {
    "reply_interested": "email_reply_interested",
    "reply_not_interested": None,
    "inquiry_new": "email_inquiry_new",
    "meeting_request": "email_meeting_request",
    "support_request": "email_support_request",
    "payment_notification": "email_payment_received",
    "internal": None,
    "spam_or_newsletter": None,
}

_CLASSIFY_PROMPT = """\
あなたはメール分類AIです。以下のメールを分析し、JSONで分類結果を返してください。

分類カテゴリ（1つだけ選択）:
- reply_interested: アウトリーチ/営業メールへの返信で、興味・質問・前向きな反応がある
- reply_not_interested: アウトリーチへの返信だが、お断り・興味なし
- inquiry_new: 新規の問い合わせ（サービスについて知りたい等）
- meeting_request: 商談/面談/打ち合わせのリクエスト
- support_request: 既存顧客からのサポート問い合わせ/トラブル報告
- payment_notification: 入金確認/請求関連の通知
- internal: 社内メール/自動通知/システムメール
- spam_or_newsletter: スパム/メルマガ/広告

メール情報:
- From: {from_addr}
- Subject: {subject}
- Body（先頭500文字）: {body_preview}

JSON形式で回答（他のテキストは不要）:
{{
  "category": "カテゴリ名",
  "confidence": 0.0-1.0,
  "summary": "1行要約",
  "urgency": "high" | "medium" | "low",
  "suggested_action": "schedule_meeting" | "reply_draft" | "create_ticket" | "forward_sales" | "ignore"
}}"""


async def classify_email(message: dict[str, Any]) -> dict[str, Any]:
    """受信メールをLLMで分類し、event_type + メタデータを返す。

    Args:
        message: gmail_watch.process_gmail_notification の戻り値の1要素
            {id, threadId, from, to, subject, snippet, body_text}

    Returns:
        {
            "event_type": str | None (None=スキップ対象),
            "category": str,
            "confidence": float,
            "summary": str,
            "urgency": str,
            "suggested_action": str,
            "message_id": str,
            "thread_id": str,
            "from": str,
            "subject": str,
        }
    """
    from_addr = message.get("from", "")
    subject = message.get("subject", "")
    body = message.get("body_text", "") or message.get("snippet", "")
    body_preview = body[:500]

    prompt = _CLASSIFY_PROMPT.format(
        from_addr=from_addr,
        subject=subject,
        body_preview=body_preview,
    )

    try:
        from llm.client import LLMTask, call_llm
        from shared.enums import ModelTier

        task = LLMTask(
            messages=[{"role": "user", "content": prompt}],
            tier=ModelTier.FAST,
            max_tokens=256,
            temperature=0.1,
            task_type="email_classification",
        )
        response = await call_llm(task)

        # JSON部分を抽出
        content = response.content.strip()
        json_match = re.search(r"\{[^{}]+\}", content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(content)

        category = result.get("category", "spam_or_newsletter")
        event_type = CATEGORY_TO_EVENT.get(category)

        return {
            "event_type": event_type,
            "category": category,
            "confidence": float(result.get("confidence", 0.5)),
            "summary": result.get("summary", ""),
            "urgency": result.get("urgency", "low"),
            "suggested_action": result.get("suggested_action", "ignore"),
            "message_id": message.get("id", ""),
            "thread_id": message.get("threadId", ""),
            "from": from_addr,
            "subject": subject,
        }

    except Exception as e:
        logger.error("email_classifier failed: %s", e)
        # フォールバック: キーワードベースの簡易分類
        return _fallback_classify(message)


def _fallback_classify(message: dict[str, Any]) -> dict[str, Any]:
    """LLMエラー時のキーワードベースフォールバック分類。"""
    subject = (message.get("subject", "") or "").lower()
    body = (message.get("body_text", "") or message.get("snippet", "")).lower()
    text = f"{subject} {body}"

    category = "spam_or_newsletter"
    urgency = "low"

    if any(kw in text for kw in ["興味", "詳しく", "検討", "教えて", "資料"]):
        category = "reply_interested"
        urgency = "high"
    elif any(kw in text for kw in ["打ち合わせ", "商談", "面談", "ミーティング", "mtg"]):
        category = "meeting_request"
        urgency = "high"
    elif any(kw in text for kw in ["問い合わせ", "問合せ", "お問い合わせ"]):
        category = "inquiry_new"
        urgency = "medium"
    elif any(kw in text for kw in ["エラー", "不具合", "困って", "トラブル", "障害"]):
        category = "support_request"
        urgency = "high"
    elif any(kw in text for kw in ["入金", "振込", "支払", "請求"]):
        category = "payment_notification"
        urgency = "medium"
    elif any(kw in text for kw in ["結構です", "不要", "お断り", "辞退"]):
        category = "reply_not_interested"

    event_type = CATEGORY_TO_EVENT.get(category)

    return {
        "event_type": event_type,
        "category": category,
        "confidence": 0.4,
        "summary": f"[fallback] {message.get('subject', '')}",
        "urgency": urgency,
        "suggested_action": "ignore" if event_type is None else "forward_sales",
        "message_id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
    }

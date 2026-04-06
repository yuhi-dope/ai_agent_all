"""LLMで企業ごとにメール文面生成"""

from __future__ import annotations

import json

import google.generativeai as genai

from config import settings

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")


class EmailContent:
    def __init__(self, subject: str, body_html: str):
        self.subject = subject
        self.body_html = body_html


async def generate_initial_email(company: dict, lp_url: str) -> EmailContent:
    """初回メール（フォーム営業 or コールドメール）を生成"""
    prompt = f"""以下の企業向けの営業メールを生成してください。

企業名: {company.get('name', '')}
業種: {company.get('industry', '')}
推定ペイン: {company.get('pain_points', [])}

メールの要件:
- 件名は15文字以内（企業名を含む）
- 本文は「突然のご連絡失礼いたします」で始める
- 企業の痛みに触れつつ、LP（職務経歴書）への誘導をする
- LP URL: {lp_url}
- 送信者: {settings.sender_name}（{settings.company_name}）
- 配信停止リンク: {settings.lp_base_url}/unsubscribe/{company.get('id', '')}
- 短く簡潔に（200文字以内）

JSON形式で返してください:
{{"subject": "件名", "body_html": "HTML本文"}}"""

    response = await model.generate_content_async(prompt)
    try:
        data = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
        return EmailContent(subject=data["subject"], body_html=data["body_html"])
    except (json.JSONDecodeError, KeyError):
        return EmailContent(
            subject=f"{company.get('name', '')}様にAI社員をご紹介",
            body_html=_fallback_email(company, lp_url),
        )


async def generate_followup_email(company: dict, sequence_num: int) -> EmailContent:
    """フォローメール生成（3日後/7日後）"""
    if sequence_num == 1:
        subject = f"【{company.get('industry', '')}】事務作業時間を80%削減した事例"
        template = "3day_followup"
    else:
        subject = f"{company.get('name', '')}様へ最後のご案内です"
        template = "7day_final"

    # TODO: LLMでカスタマイズ。現在はテンプレートベース
    return EmailContent(subject=subject, body_html=f"<!-- {template} -->")


def _fallback_email(company: dict, lp_url: str) -> str:
    name = company.get("name", "御社")
    return f"""
    <p>{name}株式会社<br>ご担当者様</p>
    <p>突然のご連絡失礼いたします。<br>
    AI業務アシスタント「シャチョツー」の{settings.sender_name}と申します。</p>
    <p>御社専用のAI社員がどんな業務をお手伝いできるか、<br>
    1枚の「職務経歴書」にまとめました。</p>
    <p><a href="{lp_url}">▼ AI社員の職務経歴書を見る（30秒で読めます）</a></p>
    <hr>
    <p>{settings.sender_name}<br>{settings.company_name}</p>
    """

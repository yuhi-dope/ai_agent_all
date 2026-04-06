"""LLMで職務経歴書テキスト生成"""

from __future__ import annotations

import json

import google.generativeai as genai

from config import settings
from research.enricher import INDUSTRY_MAPPING, determine_scale_tone
from research.models import CompanyResearch, PainPoint
from resume.models import Benefit, ResumeData

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")


async def generate_resume(company: CompanyResearch, pain_points: list[PainPoint]) -> ResumeData:
    """企業情報+痛みから職務経歴書を生成"""
    industry_data = _get_industry_data(company.industry)
    scale = determine_scale_tone(company.employee_count)
    pain_text = "\n".join(f"- {p.detail}（{p.appeal_message}）" for p in pain_points)

    prompt = f"""以下の企業情報から、AI社員「シャチョツー」の職務経歴書を生成してください。

企業名: {company.name}
業種: {company.industry}
従業員数: {company.employee_count or '不明'}名
規模感: {scale}
推定ペイン:
{pain_text}

業種の業務リスト: {json.dumps(industry_data['tasks'], ensure_ascii=False)}

以下のJSON形式で回答してください:
{{
  "strong_tasks": ["すぐ対応可能な業務を3つ（痛みに最も合うもの）"],
  "learnable_tasks": ["学習後に対応可能な業務を2つ"],
  "benefits": [
    {{"icon": "📊", "text": "具体的な削減時間の試算"}},
    {{"icon": "💰", "text": "コスト削減効果"}},
    {{"icon": "適切な絵文字", "text": "業種特有のメリット"}}
  ],
  "appeal_message": "この企業の規模感（{scale}）に合った1文の訴求メッセージ"
}}

JSON のみを返してください。"""

    response = await model.generate_content_async(prompt)
    try:
        data = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
        return ResumeData(
            company_name=company.name,
            industry=company.industry,
            strong_tasks=data["strong_tasks"],
            learnable_tasks=data["learnable_tasks"],
            benefits=[Benefit(**b) for b in data["benefits"]],
            appeal_message=data["appeal_message"],
            scale_tone=scale,
        )
    except (json.JSONDecodeError, KeyError):
        # フォールバック: テンプレートベースで生成
        return _fallback_resume(company, industry_data, scale)


def _get_industry_data(industry: str) -> dict:
    for key, val in INDUSTRY_MAPPING.items():
        if key in industry:
            return val
    return {"tasks": ["業務効率化", "データ入力自動化", "レポート自動生成"], "appeal": "業務時間を大幅に削減"}


def _fallback_resume(company: CompanyResearch, industry_data: dict, scale: str) -> ResumeData:
    tasks = industry_data["tasks"]
    return ResumeData(
        company_name=company.name,
        industry=company.industry,
        strong_tasks=tasks[:3],
        learnable_tasks=tasks[3:5],
        benefits=[
            Benefit(icon="📊", text="月40時間の事務作業削減（年間480時間）"),
            Benefit(icon="💰", text="人件費換算で年間約200万円の効果"),
            Benefit(icon="🚀", text=industry_data["appeal"]),
        ],
        appeal_message=industry_data["appeal"],
        scale_tone=scale,
    )

"""LLMでフォームフィールド↔デフォルト値マッピング"""

from __future__ import annotations

import json

import google.generativeai as genai

from config import settings
from outreach.form_analyzer import FormAnalysis

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")

STANDARD_VALUES = {
    "company_name": settings.company_name,
    "sender_name": settings.sender_name,
    "sender_email": settings.sender_email,
    "sender_phone": settings.sender_phone,
    "website_url": settings.website_url,
}


async def map_fields(
    form: FormAnalysis,
    defaults: dict[str, str],
    company_data: dict,
    message: str,
) -> tuple[dict[str, str], list[str]]:
    """フォームフィールドをデフォルト値にマッピング

    Returns:
        (mapped_values, missing_fields) — マッピング結果と不足項目
    """
    fields_desc = []
    for f in form.fields:
        desc = f"- name='{f.name}', label='{f.label}', type={f.field_type}, required={f.required}"
        if f.options:
            desc += f", options={f.options}"
        fields_desc.append(desc)

    all_values = {**STANDARD_VALUES, **defaults, "message": message}

    prompt = f"""以下のフォームフィールドに入力する値をマッピングしてください。

フォームフィールド:
{chr(10).join(fields_desc)}

利用可能な値:
{json.dumps(all_values, ensure_ascii=False, indent=2)}

企業情報（送信先）:
{json.dumps(company_data, ensure_ascii=False, indent=2)}

ルール:
- 各フィールドのname属性をキー、入力する値をバリューとしたJSONを返してください
- select/radioの場合は選択肢の中から最も近いものを選んでください
- textarea（本文）には message の値を使ってください
- マッピングできない必須フィールドは "___MISSING___" としてください

JSONのみを返してください。"""

    response = await model.generate_content_async(prompt)
    try:
        mapped = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        mapped = {}

    missing = []
    for f in form.fields:
        if f.required and mapped.get(f.name) == "___MISSING___":
            detail = f"「{f.label or f.name}」"
            if f.field_type == "select" and f.options:
                detail += f"(選択肢: {' / '.join(f.options)})"
            else:
                detail += f"({f.field_type}入力)"
            missing.append(detail)
            del mapped[f.name]

    return mapped, missing

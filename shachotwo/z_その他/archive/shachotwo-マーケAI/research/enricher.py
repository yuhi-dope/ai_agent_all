"""LLMで企業の痛み推定・業種判定"""

from __future__ import annotations

import google.generativeai as genai

from config import settings
from research.models import CompanyResearch, PainPoint

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")

INDUSTRY_MAPPING = {
    "建設業": {
        "tasks": ["積算AI", "安全書類自動作成", "出来高請求", "原価管理", "日報集計", "工程管理", "施工写真管理", "協力業者管理"],
        "appeal": "現場監督の事務作業を月40時間削減",
    },
    "製造業": {
        "tasks": ["見積AI", "生産計画", "在庫最適化", "FAX受発注", "品質管理", "出荷管理", "原価計算", "設備保全"],
        "appeal": "FAX受発注の手入力ゼロ。在庫回転率20%改善",
    },
    "歯科": {
        "tasks": ["レセプトAI", "予約最適化", "リコール管理", "カルテ入力支援", "在庫管理", "患者対応", "会計", "経営分析"],
        "appeal": "レセプト返戻率50%減。予約キャンセル率30%減",
    },
    "介護": {
        "tasks": ["ケアプラン書類", "介護報酬請求", "シフト管理", "記録業務", "モニタリング", "家族連絡", "実績管理", "LIFE対応"],
        "appeal": "記録業務2h→30分。離職原因No.1を解消",
    },
    "士業": {
        "tasks": ["顧問先Q&A", "月次巡回準備", "届出期限管理", "書類作成", "判例検索", "顧客管理", "請求管理", "ナレッジ共有"],
        "appeal": "同じ質問に何度も答える問題を解消",
    },
    "不動産": {
        "tasks": ["物件査定AI", "契約書自動作成", "賃料収支管理", "入退去管理", "修繕管理", "オーナー報告", "空室対策", "内見予約"],
        "appeal": "管理物件200→500でも人員増なし",
    },
}

SCALE_TONE = {
    "零細": {"range": (1, 10), "tone": "社長の右腕"},
    "小規模": {"range": (11, 50), "tone": "人を雇うより安い"},
    "中規模": {"range": (51, 300), "tone": "属人化リスク解消"},
}


async def estimate_pain_points(company: CompanyResearch) -> list[PainPoint]:
    """企業情報からLLMで痛みを推定"""
    job_info = "\n".join(
        f"- {jp.title}（{'急募' if jp.is_urgent else '通常'}）" for jp in company.job_postings
    ) or "求人情報なし"

    prompt = f"""以下の企業情報から、この企業が抱えている「業務上の痛み」を3つ推定してください。

企業名: {company.name}
業種: {company.industry}
従業員数: {company.employee_count or '不明'}名
事業概要: {company.business_overview}
HP抜粋: {company.raw_html[:2000]}

求人情報:
{job_info}

各痛みについて以下のJSON形式で回答してください:
[
  {{
    "category": "staffing|process|cost|compliance|growth",
    "detail": "痛みの詳細",
    "evidence": "推定根拠（何からそう判断したか）",
    "appeal_message": "この痛みに対する訴求メッセージ（1文）"
  }}
]

JSON配列のみを返してください。"""

    response = await model.generate_content_async(prompt)
    import json
    try:
        items = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
        return [PainPoint(**item) for item in items]
    except (json.JSONDecodeError, TypeError):
        return []


def determine_scale_tone(employee_count: int | None) -> str:
    """企業規模から提案トーンを決定"""
    if not employee_count:
        return "小規模"
    for scale, info in SCALE_TONE.items():
        low, high = info["range"]
        if low <= employee_count <= high:
            return scale
    return "中規模"


def get_industry_tasks(industry: str) -> dict:
    """業種から業務リスト・訴求ポイントを取得"""
    for key, val in INDUSTRY_MAPPING.items():
        if key in industry:
            return val
    return {"tasks": [], "appeal": ""}

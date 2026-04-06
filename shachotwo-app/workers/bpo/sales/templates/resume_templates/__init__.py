"""resume_templates — 業種別AI社員の職務経歴書テンプレート + LLM生成ロジック。"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Pydantic モデル
# ---------------------------------------------------------------------------

class Benefit(BaseModel):
    """メリット試算1件"""
    icon: str
    text: str


class ResumeData(BaseModel):
    """生成された職務経歴書データ"""
    company_name: str
    contact_name: str = ""
    industry: str
    monthly_price: str = "30,000"
    strong_tasks: list[str]       # すぐ対応可能な業務
    learnable_tasks: list[str]    # 学習後に対応可能な業務
    benefits: list[Benefit]       # メリット試算
    appeal_message: str           # 訴求メッセージ
    scale_tone: str               # 零細/小規模/中規模


class IndustryTemplate(BaseModel):
    """業種テンプレート定義"""
    code: str        # construction / manufacturing / dental / care / professional / realestate
    name: str        # 建設業 / 製造業 / ...
    tasks: list[str] # 全業務リスト（8つ）
    appeal_template: str  # 訴求テンプレート


# ---------------------------------------------------------------------------
# 業種マッピング（enricher.py から移植）
# ---------------------------------------------------------------------------

INDUSTRY_MAPPING: dict[str, dict[str, Any]] = {
    "建設業": {
        "code": "construction",
        "tasks": ["積算AI", "安全書類自動作成", "出来高請求", "原価管理", "日報集計", "工程管理", "施工写真管理", "協力業者管理"],
        "appeal": "現場監督の事務作業を月40時間削減",
    },
    "製造業": {
        "code": "manufacturing",
        "tasks": ["見積AI", "生産計画", "在庫最適化", "FAX受発注", "品質管理", "出荷管理", "原価計算", "設備保全"],
        "appeal": "FAX受発注の手入力ゼロ。在庫回転率20%改善",
    },
    "歯科": {
        "code": "dental",
        "tasks": ["レセプトAI", "予約最適化", "リコール管理", "カルテ入力支援", "在庫管理", "患者対応", "会計", "経営分析"],
        "appeal": "レセプト返戻率50%減。予約キャンセル率30%減",
    },
    "介護": {
        "code": "care",
        "tasks": ["ケアプラン書類", "介護報酬請求", "シフト管理", "記録業務", "モニタリング", "家族連絡", "実績管理", "LIFE対応"],
        "appeal": "記録業務2h→30分。離職原因No.1を解消",
    },
    "士業": {
        "code": "professional",
        "tasks": ["顧問先Q&A", "月次巡回準備", "届出期限管理", "書類作成", "判例検索", "顧客管理", "請求管理", "ナレッジ共有"],
        "appeal": "同じ質問に何度も答える問題を解消",
    },
    "不動産": {
        "code": "realestate",
        "tasks": ["物件査定AI", "契約書自動作成", "賃料収支管理", "入退去管理", "修繕管理", "オーナー報告", "空室対策", "内見予約"],
        "appeal": "管理物件200→500でも人員増なし",
    },
}

SCALE_TONE: dict[str, dict[str, Any]] = {
    "零細": {"range": (1, 10), "tone": "社長の右腕"},
    "小規模": {"range": (11, 50), "tone": "人を雇うより安い"},
    "中規模": {"range": (51, 300), "tone": "属人化リスク解消"},
}


# ---------------------------------------------------------------------------
# テンプレートファイル読み込み
# ---------------------------------------------------------------------------

def load_template(industry_code: str) -> str | None:
    """業種コードに対応する .md テンプレートを読み込む。見つからなければ None。"""
    path = TEMPLATES_DIR / f"{industry_code}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# LLM レジュメ生成
# ---------------------------------------------------------------------------

def _get_industry_data(industry: str) -> dict[str, Any]:
    """業種文字列から業務リスト・訴求ポイントを取得"""
    for key, val in INDUSTRY_MAPPING.items():
        if key in industry:
            return val
    return {
        "code": "general",
        "tasks": ["業務効率化", "データ入力自動化", "レポート自動生成"],
        "appeal": "業務時間を大幅に削減",
    }


def _determine_scale(employee_count: int | None) -> str:
    """企業規模判定"""
    if not employee_count:
        return "小規模"
    for scale, info in SCALE_TONE.items():
        low, high = info["range"]
        if low <= employee_count <= high:
            return scale
    return "中規模"


async def generate_resume(
    company_name: str,
    industry: str,
    employee_count: int | None = None,
    pain_points: list[dict[str, str]] | None = None,
    company_id: str | None = None,
) -> ResumeData:
    """企業情報 + 痛みから職務経歴書データを LLM で生成する。

    Args:
        company_name: 企業名
        industry: 業種
        employee_count: 従業員数
        pain_points: ペインポイント（detail, appeal_message キー）
        company_id: テナントID（LLMコスト追跡用）

    Returns:
        ResumeData
    """
    llm = get_llm_client()
    industry_data = _get_industry_data(industry)
    scale = _determine_scale(employee_count)

    pain_text = ""
    if pain_points:
        pain_text = "\n".join(
            f"- {p.get('detail', '')}（{p.get('appeal_message', '')}）"
            for p in pain_points
        )

    prompt = f"""以下の企業情報から、AI社員「シャチョツー」の職務経歴書を生成してください。

企業名: {company_name}
業種: {industry}
従業員数: {employee_count or '不明'}名
規模感: {scale}
推定ペイン:
{pain_text or '（データなし）'}

業種の業務リスト: {json.dumps(industry_data['tasks'], ensure_ascii=False)}

以下のJSON形式で回答してください:
{{
  "strong_tasks": ["すぐ対応可能な業務を3つ（痛みに最も合うもの）"],
  "learnable_tasks": ["学習後に対応可能な業務を2つ"],
  "benefits": [
    {{"icon": "適切な絵文字", "text": "具体的な削減時間の試算"}},
    {{"icon": "適切な絵文字", "text": "コスト削減効果"}},
    {{"icon": "適切な絵文字", "text": "業種特有のメリット"}}
  ],
  "appeal_message": "この企業の規模感（{scale}）に合った1文の訴求メッセージ"
}}

JSON のみを返してください。"""

    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": "あなたはBtoB営業の専門家です。JSON のみを返してください。"},
                {"role": "user", "content": prompt},
            ],
            tier=ModelTier.FAST,
            task_type="resume_generator",
            company_id=company_id,
            temperature=0.3,
        ))

        raw = response.content.strip()
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            return _fallback_resume(company_name, industry, industry_data, scale)

        data = json.loads(json_match.group())
        return ResumeData(
            company_name=company_name,
            industry=industry,
            strong_tasks=data.get("strong_tasks", industry_data["tasks"][:3]),
            learnable_tasks=data.get("learnable_tasks", industry_data["tasks"][3:5]),
            benefits=[Benefit(**b) for b in data.get("benefits", [])],
            appeal_message=data.get("appeal_message", industry_data["appeal"]),
            scale_tone=scale,
        )

    except Exception as e:
        logger.error(f"resume generation failed, using fallback: {e}")
        return _fallback_resume(company_name, industry, industry_data, scale)


def _fallback_resume(
    company_name: str,
    industry: str,
    industry_data: dict[str, Any],
    scale: str,
) -> ResumeData:
    """LLM 失敗時のフォールバック"""
    tasks = industry_data["tasks"]
    return ResumeData(
        company_name=company_name,
        industry=industry,
        strong_tasks=tasks[:3] if len(tasks) >= 3 else tasks,
        learnable_tasks=tasks[3:5] if len(tasks) >= 5 else [],
        benefits=[
            Benefit(icon="clock", text="月40時間の事務作業削減（年間480時間）"),
            Benefit(icon="yen", text="人件費換算で年間約200万円の効果"),
            Benefit(icon="rocket", text=industry_data["appeal"]),
        ],
        appeal_message=industry_data["appeal"],
        scale_tone=scale,
    )

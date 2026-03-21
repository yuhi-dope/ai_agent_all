"""オンボーディングフロー — 業種に応じたテンプレート適用 + 初回Q&Aガイド。

Day 1 Value: 登録から15分以内に「使える」と感じさせる。
1. 会社の業種を確認
2. 業種テンプレートを適用（ナレッジ初期データ投入）
3. 試しのQ&Aを案内（テンプレート内のナレッジで回答できる質問を提示）
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import require_role
from auth.jwt import JWTClaims
from brain.genome.applicator import apply_template
from brain.genome.templates import get_template, list_templates, get_template_for_industry
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Models --

class OnboardingStatusResponse(BaseModel):
    company_id: str
    industry: str | None
    template_applied: bool
    knowledge_count: int
    onboarding_progress: float  # 0.0 - 1.0
    next_step: str
    suggested_questions: list[str]


class ApplyIndustryTemplateRequest(BaseModel):
    industry: str  # construction / manufacturing / dental / food / beauty / logistics / ec / care / realestate


INDUSTRY_QUESTIONS: dict[str, list[str]] = {
    "construction": [
        "見積もりの諸経費率はいくらですか？",
        "天候による作業中止の基準は？",
        "経費精算の承認フローは？",
        "新規入場者教育の内容は？",
        "下請業者の評価基準は？",
    ],
    "manufacturing": [
        "見積の標準利益率は？",
        "材料の仕入先と単価は？",
        "主要設備のチャージレートは？",
        "品質検査の基準は？",
        "外注先の選定基準は？",
    ],
    "dental": [
        "初診患者の対応フローは？",
        "自費診療の提案基準は？",
        "投薬の第一選択は？",
        "院内感染対策のルールは？",
        "リコールの間隔は？",
    ],
}


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    user: JWTClaims = Depends(require_role("admin", "editor")),
):
    """オンボーディング状態を取得"""
    db = get_service_client()

    # 会社情報
    company = db.table("companies").select(
        "industry, genome_customizations, onboarding_progress"
    ).eq("id", user.company_id).single().execute()

    c = company.data
    industry = c.get("industry", "")
    customizations = c.get("genome_customizations") or {}
    template_applied = bool(customizations.get("applied_template"))

    # ナレッジ数
    knowledge = db.table("knowledge_items").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    knowledge_count = knowledge.count or 0

    # 進捗計算
    progress = 0.0
    if industry:
        progress += 0.2
    if template_applied:
        progress += 0.4
    if knowledge_count > 0:
        progress += 0.2
    if knowledge_count >= 10:
        progress += 0.2

    # 次のステップ
    if not industry:
        next_step = "業種を選択してください"
    elif not template_applied:
        next_step = "業種テンプレートを適用してください"
    elif knowledge_count == 0:
        next_step = "テンプレートを適用中です。少しお待ちください"
    elif knowledge_count < 10:
        next_step = "Q&Aを試してみてください。下の質問例をクリック！"
    else:
        next_step = "独自のナレッジを追加して、さらに精度を上げましょう"

    # 推奨質問
    suggested = INDUSTRY_QUESTIONS.get(industry, [
        "社内の承認フローは？",
        "経費精算のルールは？",
        "残業申請のルールは？",
    ])

    return OnboardingStatusResponse(
        company_id=user.company_id,
        industry=industry,
        template_applied=template_applied,
        knowledge_count=knowledge_count,
        onboarding_progress=progress,
        next_step=next_step,
        suggested_questions=suggested,
    )


@router.post("/onboarding/apply-template")
async def apply_industry_template(
    body: ApplyIndustryTemplateRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """
    業種テンプレートを適用

    1. companiesのindustryを更新
    2. テンプレートを適用（ナレッジ一括挿入+embedding生成）
    3. オンボーディング進捗を更新
    """
    db = get_service_client()

    # industry更新
    db.table("companies").update({
        "industry": body.industry,
    }).eq("id", user.company_id).execute()

    # テンプレート適用
    template = get_template(body.industry)
    if not template:
        # industryからテンプレートを探す
        template = get_template_for_industry(body.industry)

    if not template:
        raise HTTPException(status_code=404, detail="テンプレートが見つかりません")

    result = await apply_template(
        template_id=template.id,
        company_id=user.company_id,
    )

    # 進捗更新
    db.table("companies").update({
        "onboarding_progress": 0.6,
    }).eq("id", user.company_id).execute()

    return {
        "template_id": result.template_id,
        "items_created": result.items_created,
        "departments": result.departments,
        "message": f"{result.items_created}件のナレッジが追加されました。Q&Aを試してみてください！",
        "suggested_questions": INDUSTRY_QUESTIONS.get(body.industry, []),
    }

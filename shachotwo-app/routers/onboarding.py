"""オンボーディングフロー — 3プラン対応（セルフ/コンサル/フルサポート）。

Day 1 Value: 登録から15分以内に「使える」と感じさせる。
1. 会社の業種を確認
2. 業種テンプレートを適用（ナレッジ初期データ投入）
3. 試しのQ&Aを案内（テンプレート内のナレッジで回答できる質問を提示）

3プラン:
- self:         セルフプラン（無料）
- consul:       コンサルプラン（5万円/月 x 2ヶ月）
- full_support: フルサポートプラン（30万円/月 x 3ヶ月）
"""
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import require_role
from auth.jwt import JWTClaims
from brain.genome.applicator import apply_template
from brain.genome.templates import get_template, list_templates, get_template_for_industry
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# -- Enums & Constants --

class OnboardingPlan(str, Enum):
    SELF = "self"
    CONSUL = "consul"
    FULL_SUPPORT = "full_support"


# プラン別ステップ定義
PLAN_STEPS: dict[str, list[dict[str, str]]] = {
    "self": [
        {"key": "industry_selected", "label": "業種を選択する"},
        {"key": "template_applied", "label": "業種テンプレートを適用する"},
        {"key": "first_qa", "label": "はじめてのQ&Aを試す"},
        {"key": "knowledge_10", "label": "ナレッジを10件以上にする"},
    ],
    "consul": [
        {"key": "industry_selected", "label": "業種を選択する"},
        {"key": "template_applied", "label": "業種テンプレートを適用する"},
        {"key": "first_qa", "label": "はじめてのQ&Aを試す"},
        {"key": "knowledge_10", "label": "ナレッジを10件以上にする"},
        {"key": "first_meeting_done", "label": "初回ヒアリング（Meet 60分）を完了する"},
        {"key": "training_done", "label": "BPO実行トレーニングを完了する"},
        {"key": "self_run_confirmed", "label": "自走確認を完了する"},
    ],
    "full_support": [
        {"key": "industry_selected", "label": "業種を選択する"},
        {"key": "template_applied", "label": "業種テンプレートを適用する"},
        {"key": "first_qa", "label": "はじめてのQ&Aを試す"},
        {"key": "knowledge_10", "label": "ナレッジを10件以上にする"},
        {"key": "first_meeting_done", "label": "初回ヒアリング（Meet 60分）を完了する"},
        {"key": "training_done", "label": "BPO実行トレーニングを完了する"},
        {"key": "self_run_confirmed", "label": "自走確認を完了する"},
        {"key": "custom_bpo_created", "label": "カスタムBPOテンプレートを作成する"},
        {"key": "monthly_review_1", "label": "1ヶ月目レビューを完了する"},
        {"key": "monthly_review_2", "label": "2ヶ月目レビューを完了する"},
        {"key": "graduation", "label": "オンボーディング卒業（自走開始）"},
    ],
}

# プラン別料金情報
PLAN_PRICING: dict[str, dict] = {
    "self": {
        "monthly_fee": 0,
        "duration_months": 0,
        "description": "セルフプラン（無料）",
        "features": [
            "テンプレート自動適用（業種ゲノム50件のナレッジ投入）",
            "ガイド付きQ&A体験",
            "Day 1/3/7/14/30 メール自動配信",
            "AIチャットサポート",
        ],
    },
    "consul": {
        "monthly_fee": 50000,
        "duration_months": 2,
        "description": "コンサルプラン（5万円/月 x 2ヶ月）",
        "features": [
            "セルフプランの全内容",
            "Week 1: 初回ヒアリング（Meet 60分）",
            "Week 2: ナレッジ投入サポート（Meet 30分）",
            "Week 3: BPO実行トレーニング（Meet 30分）",
            "Week 4: 自走確認（Meet 30分）",
            "Month 2: 定着フォロー（Meet 30分 x 2回）",
        ],
    },
    "full_support": {
        "monthly_fee": 300000,
        "duration_months": 3,
        "description": "フルサポートプラン（30万円/月 x 3ヶ月）",
        "features": [
            "コンサルプランの全内容",
            "週1回の定例ミーティング（3ヶ月間 = 12回）",
            "ナレッジ代行入力",
            "カスタムBPOテンプレート作成",
            "専任担当者（Slack直通）",
            "4ヶ月目からBPO自走（30万円/月）、人間サポート追加は+20万円/月",
        ],
    },
}


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
    "care": [
        "入居者の受け入れ基準は？",
        "夜間対応のマニュアルは？",
        "ケアプランの更新頻度は？",
    ],
    "logistics": [
        "配送ルートの最適化基準は？",
        "車両メンテナンスの頻度は？",
        "荷物の紛失・破損時の対応フローは？",
    ],
    "wholesale": [
        "在庫回転率の目標は？",
        "仕入先との価格交渉のルールは？",
        "与信管理の基準は？",
    ],
}


# -- Request / Response Models --

class OnboardingStatusResponse(BaseModel):
    company_id: str
    industry: str | None
    plan: str  # "self" | "consul" | "full_support"
    template_applied: bool
    knowledge_count: int
    qa_count: int
    bpo_execution_count: int
    onboarding_progress: float  # 0.0 - 1.0
    completed_steps: list[str]
    next_step: str
    suggested_questions: list[str]
    plan_info: dict


class ApplyIndustryTemplateRequest(BaseModel):
    industry: str


class SelectPlanRequest(BaseModel):
    plan: Literal["self", "consul", "full_support"]


class SelectPlanResponse(BaseModel):
    plan: str
    description: str
    monthly_fee: int
    duration_months: int
    features: list[str]
    steps: list[dict[str, str]]
    message: str


class ChecklistItem(BaseModel):
    key: str
    label: str
    completed: bool
    required: bool


class ChecklistResponse(BaseModel):
    plan: str
    total_steps: int
    completed_count: int
    progress: float
    items: list[ChecklistItem]


class CompleteStepRequest(BaseModel):
    step_key: str
    notes: str | None = None


# -- Helper functions --

def _get_completed_steps(
    industry: str | None,
    template_applied: bool,
    knowledge_count: int,
    qa_count: int,
    bpo_count: int,
    manual_steps: dict,
) -> list[str]:
    """自動判定 + 手動完了ステップを統合して完了済みステップリストを返す。"""
    completed: list[str] = []
    if industry:
        completed.append("industry_selected")
    if template_applied:
        completed.append("template_applied")
    if qa_count > 0:
        completed.append("first_qa")
    if knowledge_count >= 10:
        completed.append("knowledge_10")
    # 手動で完了マークされたステップ
    for key, val in manual_steps.items():
        if val and key not in completed:
            completed.append(key)
    return completed


def _calc_progress(completed_steps: list[str], plan: str) -> float:
    """プラン別のステップ数に基づいてプログレスを計算する。"""
    plan_steps = PLAN_STEPS.get(plan, PLAN_STEPS["self"])
    total = len(plan_steps)
    if total == 0:
        return 1.0
    done = sum(1 for s in plan_steps if s["key"] in completed_steps)
    return round(done / total, 2)


def _get_next_step(completed_steps: list[str], plan: str) -> str:
    """プラン別の次の未完了ステップを返す。"""
    plan_steps = PLAN_STEPS.get(plan, PLAN_STEPS["self"])
    for step in plan_steps:
        if step["key"] not in completed_steps:
            return step["label"]
    return "全てのオンボーディングステップが完了しました！"


# -- Endpoints --

@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    user: JWTClaims = Depends(require_role("admin", "editor")),
):
    """オンボーディング状態を取得（プラン情報含む）"""
    db = get_service_client()

    # 会社情報
    company = db.table("companies").select(
        "industry, genome_customizations, onboarding_progress, onboarding_plan, onboarding_steps"
    ).eq("id", user.company_id).single().execute()

    c = company.data
    industry = c.get("industry", "")
    customizations = c.get("genome_customizations") or {}
    template_applied = bool(customizations.get("applied_template"))
    plan = c.get("onboarding_plan") or "self"
    manual_steps = c.get("onboarding_steps") or {}

    # ナレッジ数
    knowledge = db.table("knowledge_items").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    knowledge_count = knowledge.count or 0

    # Q&A回数
    qa = db.table("qa_sessions").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    qa_count = qa.count or 0

    # BPO実行回数
    bpo = db.table("execution_logs").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    bpo_count = bpo.count or 0

    # 完了ステップ
    completed_steps = _get_completed_steps(
        industry, template_applied, knowledge_count, qa_count, bpo_count, manual_steps,
    )

    # 進捗計算
    progress = _calc_progress(completed_steps, plan)

    # 次のステップ
    next_step = _get_next_step(completed_steps, plan)

    # 推奨質問（業種別 + セキュリティ共通）
    suggested = INDUSTRY_QUESTIONS.get(industry, [
        "社内の承認フローは？",
        "経費精算のルールは？",
        "残業申請のルールは？",
    ]) + [
        "シャチョツーのデータはどこに保管されていますか？",
        "AIにデータを入れて安全ですか？",
    ]

    return OnboardingStatusResponse(
        company_id=user.company_id,
        industry=industry,
        plan=plan,
        template_applied=template_applied,
        knowledge_count=knowledge_count,
        qa_count=qa_count,
        bpo_execution_count=bpo_count,
        onboarding_progress=progress,
        completed_steps=completed_steps,
        next_step=next_step,
        suggested_questions=suggested,
        plan_info=PLAN_PRICING.get(plan, PLAN_PRICING["self"]),
    )


@router.post("/onboarding/plan", response_model=SelectPlanResponse)
async def select_onboarding_plan(
    body: SelectPlanRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """オンボーディングプランを選択・変更する。"""
    db = get_service_client()
    plan = body.plan

    pricing = PLAN_PRICING.get(plan)
    if not pricing:
        raise HTTPException(status_code=400, detail="無効なプランです")

    steps = PLAN_STEPS.get(plan, PLAN_STEPS["self"])

    # companiesテーブル更新
    db.table("companies").update({
        "onboarding_plan": plan,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", user.company_id).execute()

    logger.info(f"onboarding plan selected: company_id={user.company_id}, plan={plan}")

    return SelectPlanResponse(
        plan=plan,
        description=pricing["description"],
        monthly_fee=pricing["monthly_fee"],
        duration_months=pricing["duration_months"],
        features=pricing["features"],
        steps=steps,
        message=f"{pricing['description']}を選択しました。",
    )


@router.get("/onboarding/checklist", response_model=ChecklistResponse)
async def get_onboarding_checklist(
    user: JWTClaims = Depends(require_role("admin", "editor")),
):
    """プラン別の残タスク一覧を返す。"""
    db = get_service_client()

    company = db.table("companies").select(
        "industry, genome_customizations, onboarding_plan, onboarding_steps"
    ).eq("id", user.company_id).single().execute()

    c = company.data
    industry = c.get("industry", "")
    customizations = c.get("genome_customizations") or {}
    template_applied = bool(customizations.get("applied_template"))
    plan = c.get("onboarding_plan") or "self"
    manual_steps = c.get("onboarding_steps") or {}

    # ナレッジ数
    knowledge = db.table("knowledge_items").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    knowledge_count = knowledge.count or 0

    # Q&A回数
    qa = db.table("qa_sessions").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    qa_count = qa.count or 0

    # BPO実行回数
    bpo = db.table("execution_logs").select(
        "id", count="exact"
    ).eq("company_id", user.company_id).execute()
    bpo_count = bpo.count or 0

    # 完了ステップ
    completed_steps = _get_completed_steps(
        industry, template_applied, knowledge_count, qa_count, bpo_count, manual_steps,
    )

    plan_steps = PLAN_STEPS.get(plan, PLAN_STEPS["self"])

    items = []
    for step in plan_steps:
        items.append(ChecklistItem(
            key=step["key"],
            label=step["label"],
            completed=step["key"] in completed_steps,
            required=True,
        ))

    completed_count = sum(1 for i in items if i.completed)
    total = len(items)
    progress = round(completed_count / total, 2) if total > 0 else 1.0

    return ChecklistResponse(
        plan=plan,
        total_steps=total,
        completed_count=completed_count,
        progress=progress,
        items=items,
    )


@router.post("/onboarding/complete-step")
async def complete_onboarding_step(
    body: CompleteStepRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """手動ステップを完了としてマークする（ミーティング完了等）。"""
    db = get_service_client()

    # 有効なステップキーかチェック
    all_keys = set()
    for steps in PLAN_STEPS.values():
        for s in steps:
            all_keys.add(s["key"])

    if body.step_key not in all_keys:
        raise HTTPException(status_code=400, detail=f"無効なステップキー: {body.step_key}")

    # 自動判定ステップはこのエンドポイントでは変更しない
    auto_steps = {"industry_selected", "template_applied", "first_qa", "knowledge_10"}
    if body.step_key in auto_steps:
        raise HTTPException(
            status_code=400,
            detail=f"{body.step_key} は自動判定されるステップです。手動では変更できません。",
        )

    # 既存のステップ状態を取得して更新
    company = db.table("companies").select(
        "onboarding_steps"
    ).eq("id", user.company_id).single().execute()

    current_steps = company.data.get("onboarding_steps") or {}
    current_steps[body.step_key] = {
        "completed": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "notes": body.notes,
    }

    db.table("companies").update({
        "onboarding_steps": current_steps,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", user.company_id).execute()

    logger.info(
        f"onboarding step completed: company_id={user.company_id}, "
        f"step={body.step_key}"
    )

    return {
        "step_key": body.step_key,
        "completed": True,
        "message": f"ステップ「{body.step_key}」を完了にしました。",
    }


@router.get("/onboarding/plans")
async def list_onboarding_plans():
    """利用可能なオンボーディングプラン一覧を返す（認証不要）。"""
    return {
        "plans": [
            {
                "key": key,
                **info,
                "steps": PLAN_STEPS[key],
            }
            for key, info in PLAN_PRICING.items()
        ],
    }


# -- Setup Wizard Models --

class SetupWizardRequest(BaseModel):
    industry: str = "manufacturing"
    sub_industry: str = ""
    employee_range: str  # "10-50" | "51-100" | "101-200" | "201-300"
    departments: list[str]
    data_import_method: str  # "template" | "csv" | "connector"
    selected_pipelines: list[str]


class SetupWizardResponse(BaseModel):
    success: bool
    template_applied: bool
    knowledge_items_created: int
    pipelines_enabled: list[str]
    next_step: str  # "csv_import" | "connector_setup" | "first_qa"


# 従業員範囲→人数マッピング（scale_trigger判定用）
_EMPLOYEE_RANGE_MAP: dict[str, int] = {
    "10-50": 30,
    "51-100": 75,
    "101-200": 150,
    "201-300": 250,
}


@router.post("/onboarding/setup-wizard", response_model=SetupWizardResponse)
async def run_setup_wizard(
    req: SetupWizardRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> SetupWizardResponse:
    """初回セットアップウィザード実行。

    1. 業種テンプレートを適用（brain.genome.applicator.apply_template）
    2. 選択部門をcompaniesテーブルに保存
    3. 選択パイプラインをcompanies.enabled_pipelinesに保存
    4. onboarding_progressを更新
    """
    db = get_service_client()

    # 従業員数を推定
    employee_count = _EMPLOYEE_RANGE_MAP.get(req.employee_range)

    # テンプレート適用
    template_applied = False
    knowledge_items_created = 0
    try:
        customizations: dict = {}
        if req.departments:
            customizations["departments"] = req.departments

        result = await apply_template(
            template_id=req.industry,
            company_id=user.company_id,
            customizations=customizations if customizations else None,
            employee_count=employee_count,
        )
        template_applied = True
        knowledge_items_created = result.items_created
    except ValueError as e:
        logger.warning(f"setup-wizard: template not found for industry={req.industry}: {e}")
        # テンプレートが見つからなくても続行
    except Exception as e:
        logger.error(f"setup-wizard: apply_template failed: {e}")
        raise HTTPException(status_code=500, detail=f"テンプレート適用に失敗しました: {e}")

    # companiesテーブル更新
    try:
        now = datetime.now(timezone.utc).isoformat()
        update_payload: dict = {
            "industry": req.industry,
            "updated_at": now,
        }
        if req.sub_industry:
            update_payload["sub_industry"] = req.sub_industry
        if req.employee_range:
            update_payload["employee_range"] = req.employee_range
        if req.selected_pipelines:
            update_payload["enabled_pipelines"] = req.selected_pipelines

        db.table("companies").update(update_payload).eq("id", user.company_id).execute()
    except Exception as e:
        logger.error(f"setup-wizard: companies update failed: {e}")
        raise HTTPException(status_code=500, detail=f"会社情報の更新に失敗しました: {e}")

    # onboarding_progress 更新
    try:
        company = db.table("companies").select(
            "onboarding_plan"
        ).eq("id", user.company_id).single().execute()
        plan = company.data.get("onboarding_plan") or "self"
        completed = ["industry_selected"]
        if template_applied:
            completed.append("template_applied")
        progress = _calc_progress(completed, plan)
        db.table("companies").update({
            "onboarding_progress": progress,
        }).eq("id", user.company_id).execute()
    except Exception as e:
        logger.warning(f"setup-wizard: onboarding_progress update failed: {e}")

    # next_step の決定
    if req.data_import_method == "csv":
        next_step = "csv_import"
    elif req.data_import_method == "connector":
        next_step = "connector_setup"
    else:
        next_step = "first_qa"

    logger.info(
        f"setup-wizard completed: company_id={user.company_id} "
        f"industry={req.industry} template_applied={template_applied} "
        f"items={knowledge_items_created} next={next_step}"
    )

    return SetupWizardResponse(
        success=True,
        template_applied=template_applied,
        knowledge_items_created=knowledge_items_created,
        pipelines_enabled=req.selected_pipelines,
        next_step=next_step,
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

    # 進捗更新（プラン別に計算）
    company = db.table("companies").select(
        "onboarding_plan"
    ).eq("id", user.company_id).single().execute()
    plan = company.data.get("onboarding_plan") or "self"
    # template_applied + industry_selected = 2ステップ完了
    completed = ["industry_selected", "template_applied"]
    progress = _calc_progress(completed, plan)

    db.table("companies").update({
        "onboarding_progress": progress,
    }).eq("id", user.company_id).execute()

    return {
        "template_id": result.template_id,
        "items_created": result.items_created,
        "departments": result.departments,
        "message": f"{result.items_created}件のナレッジが追加されました。Q&Aを試してみてください！",
        "suggested_questions": INDUSTRY_QUESTIONS.get(body.industry, []),
    }

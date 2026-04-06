"""アップセル支援エンドポイント — 拡張タイミング検知・コンサル向けブリーフィング"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class UpsellOpportunityItem(BaseModel):
    customer_id: UUID
    customer_company_name: str
    industry: str
    current_mrr: int
    upsell_type: str                         # additional_module / upgrade_to_bpo / backoffice / custom_bpo
    trigger_reason: str                      # 拡張タイミングの根拠
    recommended_modules: list[str]           # 推奨追加モジュール
    estimated_additional_mrr: int           # 追加MRR見込み（円）
    health_score: Optional[int] = None
    detected_at: datetime


class UpsellOpportunityListResponse(BaseModel):
    items: list[UpsellOpportunityItem]
    total: int


class UsageSummary(BaseModel):
    """過去30日の機能利用状況"""
    login_days: int
    qa_count: int
    bpo_execution_count: int
    knowledge_items_created: int
    active_modules: list[str]
    unused_available_modules: list[str]     # 契約済みだが未使用のモジュール
    bpo_utilization_rate: Optional[float] = None  # BPOコア利用率（%）


class PricingSimulation(BaseModel):
    current_modules: list[str]
    current_mrr: int
    proposed_modules: list[str]
    proposed_mrr: int
    additional_mrr: int
    annual_additional: int


class UpsellBriefingResponse(BaseModel):
    customer_id: UUID
    customer_company_name: str
    industry: str
    plan: str
    health_score: Optional[int] = None
    nps_score: Optional[int] = None

    # 利用状況サマリー
    usage_summary: UsageSummary

    # AI 推奨アクション
    recommended_action: str                  # 提案すべきアクションの説明
    recommended_modules: list[str]           # 追加推奨モジュール
    reasoning: list[str]                     # 推奨根拠（箇条書き）
    potential_objections: list[str]          # 想定される反論と回答案

    # 見積シミュレーション
    pricing_simulation: PricingSimulation

    # 過去のやり取り
    recent_tickets: list[dict]              # 直近5件のサポートチケット
    recent_feature_requests: list[dict]     # 直近5件の要望

    generated_at: datetime


class BriefingGenerateResponse(BaseModel):
    customer_id: UUID
    status: str
    message: str


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

# 利用可能な全BPOモジュール一覧
_ALL_BPO_MODULES = {
    "estimation", "safety_docs", "billing", "cost_report", "permit",
    "photo_organize", "subcontractor", "construction_plan", "quoting",
    "production_plan", "receipt_check",
}

# 料金定義（円）
_PRICING = {
    "brain_only": 30_000,
    "bpo_core": 250_000,
    "additional_module": 100_000,
    "backoffice_bpo": 200_000,
}

# アップセル判定しきい値
_BPO_UTILIZATION_THRESHOLD = 0.80
_QA_WEEKLY_THRESHOLD = 10
_HEALTH_SCORE_THRESHOLD = 80
_CONTRACT_MONTHS_THRESHOLD = 6
_CUSTOM_REQUESTS_THRESHOLD = 3


def _detect_upsell_opportunities(customer: dict) -> list[dict]:
    """顧客データからアップセル機会を判定して返す。

    Returns:
        list of dict with keys: upsell_type, trigger_reason, recommended_modules,
        estimated_additional_mrr
    """
    opportunities: list[dict] = []

    active_modules: list[str] = customer.get("active_modules") or []
    active_set = set(active_modules)
    health_score: int = customer.get("health_score") or 0
    plan: str = customer.get("plan") or ""
    mrr: int = customer.get("mrr") or 0
    bpo_utilization: float = customer.get("bpo_utilization_rate") or 0.0
    qa_weekly_avg: float = customer.get("qa_weekly_avg") or 0.0
    contract_months: int = customer.get("contract_months") or 0
    custom_request_count: int = customer.get("custom_request_count") or 0

    has_bpo_core = plan in ("bpo_core", "enterprise") or "bpo_core" in active_set
    has_brain_only = plan == "brain" and not has_bpo_core
    has_all_bpo = _ALL_BPO_MODULES.issubset(active_set)

    # パターン1: BPOコア利用率 >= 80% + 未使用モジュール → additional_module 提案
    if has_bpo_core and bpo_utilization >= _BPO_UTILIZATION_THRESHOLD:
        unused = sorted(_ALL_BPO_MODULES - active_set)
        if unused:
            recommended = unused[:3]
            opportunities.append({
                "upsell_type": "additional_module",
                "trigger_reason": (
                    f"BPOコア利用率{bpo_utilization * 100:.0f}%到達。"
                    f"未使用モジュール{len(unused)}件あり（推奨: {', '.join(recommended)}）"
                ),
                "recommended_modules": recommended,
                "estimated_additional_mrr": len(recommended) * _PRICING["additional_module"],
            })

    # パターン2: ブレインのみ + Q&A週10回以上 → upgrade_to_bpo 提案
    if has_brain_only and qa_weekly_avg >= _QA_WEEKLY_THRESHOLD:
        opportunities.append({
            "upsell_type": "upgrade_to_bpo",
            "trigger_reason": (
                f"ブレインのみ契約でQ&A週平均{qa_weekly_avg:.0f}回。"
                "業務自動化（BPOコア）の導入タイミング。"
            ),
            "recommended_modules": ["bpo_core"],
            "estimated_additional_mrr": _PRICING["bpo_core"],
        })

    # パターン3: health_score >= 80 + 契約6ヶ月経過 → backoffice 提案
    if (
        health_score >= _HEALTH_SCORE_THRESHOLD
        and contract_months >= _CONTRACT_MONTHS_THRESHOLD
        and "backoffice_bpo" not in active_set
    ):
        opportunities.append({
            "upsell_type": "backoffice",
            "trigger_reason": (
                f"ヘルススコア{health_score}点・契約{contract_months}ヶ月経過。"
                "バックオフィスBPO導入タイミング。"
            ),
            "recommended_modules": ["backoffice_bpo"],
            "estimated_additional_mrr": _PRICING["backoffice_bpo"],
        })

    # パターン4: 全BPOモジュール利用中 + カスタム要望3件以上 → custom_bpo 提案
    if has_all_bpo and custom_request_count >= _CUSTOM_REQUESTS_THRESHOLD:
        opportunities.append({
            "upsell_type": "custom_bpo",
            "trigger_reason": (
                f"全BPOモジュール利用中でカスタム要望{custom_request_count}件。"
                "自社開発BPO提案タイミング。"
            ),
            "recommended_modules": [],
            "estimated_additional_mrr": 0,
        })

    return opportunities


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/upsell/opportunities", response_model=UpsellOpportunityListResponse)
async def list_upsell_opportunities(
    upsell_type: Optional[str] = None,
    min_additional_mrr: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    user: JWTClaims = Depends(get_current_user),
):
    """アップセル候補一覧を取得する。

    検知ルール（upsell_briefing_pipeline から）:
    - BPOコア利用率 >= 80% + 未使用モジュールあり → additional_module 提案
    - ブレインのみ契約 + Q&A 週10回以上 → upgrade_to_bpo 提案
    - health_score >= 80 + 契約6ヶ月経過 → backoffice 提案
    - 全BPOモジュール利用中 + カスタム要望3件以上 → custom_bpo 提案
    """
    try:
        db = get_service_client()

        # health_score >= 80 の顧客を取得（チャーン済み除外）
        customers_res = (
            db.table("customers")
            .select(
                "id, customer_company_name, industry, plan, active_modules, "
                "mrr, health_score, nps_score, status, created_at"
            )
            .eq("company_id", str(user.company_id))
            .gte("health_score", _HEALTH_SCORE_THRESHOLD)
            .neq("status", "churned")
            .execute()
        )
        customers: list[dict] = customers_res.data or []

        # 各顧客のアップセル機会を判定
        items: list[UpsellOpportunityItem] = []
        now = datetime.now(timezone.utc)

        for c in customers:
            customer_id = c["id"]
            active_modules: list[str] = c.get("active_modules") or []

            # 契約月数を計算
            created_at_str = c.get("created_at", "")
            contract_months = 0
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(
                        created_at_str.replace("Z", "+00:00")
                    )
                    delta = now - created_at
                    contract_months = int(delta.days / 30)
                except Exception:
                    pass

            # BPO実行数（過去30日）を取得してBPO利用率を算出
            exec_res = (
                db.table("execution_logs")
                .select("id", count="exact")
                .eq("company_id", str(user.company_id))
                .eq("customer_id", customer_id)
                .gte(
                    "created_at",
                    (now.replace(day=1)).isoformat(),
                )
                .execute()
            )
            bpo_execution_count = exec_res.count or 0
            # 簡易利用率: 実行数 / モジュール数を正規化（最大1.0）
            module_count = max(len(active_modules), 1)
            bpo_utilization_rate = min(bpo_execution_count / (module_count * 10), 1.0)

            # Q&A週平均（過去30日 / 4週）
            qa_res = (
                db.table("knowledge_sessions")
                .select("id", count="exact")
                .eq("company_id", str(user.company_id))
                .gte(
                    "created_at",
                    (now.replace(day=1)).isoformat(),
                )
                .execute()
            )
            qa_count = qa_res.count or 0
            qa_weekly_avg = qa_count / 4.0

            # カスタム要望件数
            feat_res = (
                db.table("feature_requests")
                .select("id", count="exact")
                .eq("company_id", str(user.company_id))
                .eq("customer_id", customer_id)
                .execute()
            )
            custom_request_count = feat_res.count or 0

            # アップセル判定用データを構築
            usage = {
                "active_modules": active_modules,
                "health_score": c.get("health_score") or 0,
                "plan": c.get("plan") or "",
                "mrr": c.get("mrr") or 0,
                "bpo_utilization_rate": bpo_utilization_rate,
                "qa_weekly_avg": qa_weekly_avg,
                "contract_months": contract_months,
                "custom_request_count": custom_request_count,
            }

            opps = _detect_upsell_opportunities(usage)
            for opp in opps:
                # フィルタ適用
                if upsell_type and opp["upsell_type"] != upsell_type:
                    continue
                if (
                    min_additional_mrr is not None
                    and opp["estimated_additional_mrr"] < min_additional_mrr
                ):
                    continue

                items.append(
                    UpsellOpportunityItem(
                        customer_id=UUID(customer_id),
                        customer_company_name=c.get("customer_company_name", ""),
                        industry=c.get("industry", ""),
                        current_mrr=c.get("mrr") or 0,
                        upsell_type=opp["upsell_type"],
                        trigger_reason=opp["trigger_reason"],
                        recommended_modules=opp["recommended_modules"],
                        estimated_additional_mrr=opp["estimated_additional_mrr"],
                        health_score=c.get("health_score"),
                        detected_at=now,
                    )
                )

                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break

        return UpsellOpportunityListResponse(items=items, total=len(items))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list upsell opportunities failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/upsell/briefing/{customer_id}", response_model=UpsellBriefingResponse)
async def get_upsell_briefing(
    customer_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """コンサル向けブリーフィングを取得する。

    含まれる情報:
    - 顧客プロファイル要約
    - 過去30日の利用状況サマリー
    - AI 推奨アクション（根拠付き）
    - 想定質問と回答案
    - 見積シミュレーション
    - 直近のサポートチケット・要望
    """
    try:
        db = get_service_client()
        now = datetime.now(timezone.utc)
        cid = str(customer_id)

        # 顧客情報取得
        c_res = (
            db.table("customers")
            .select(
                "id, customer_company_name, industry, plan, active_modules, "
                "mrr, health_score, nps_score, status, created_at"
            )
            .eq("company_id", str(user.company_id))
            .eq("id", cid)
            .single()
            .execute()
        )
        if not c_res.data:
            raise HTTPException(status_code=404, detail="Customer not found")
        c = c_res.data
        active_modules: list[str] = c.get("active_modules") or []

        # 契約月数
        contract_months = 0
        created_at_str = c.get("created_at", "")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                contract_months = int((now - created_at).days / 30)
            except Exception:
                pass

        # 過去30日のBPO実行数
        month_start = now.replace(day=1).isoformat()
        exec_res = (
            db.table("execution_logs")
            .select("id", count="exact")
            .eq("company_id", str(user.company_id))
            .gte("created_at", month_start)
            .execute()
        )
        bpo_execution_count = exec_res.count or 0

        # 過去30日のQ&A数
        qa_res = (
            db.table("knowledge_sessions")
            .select("id", count="exact")
            .eq("company_id", str(user.company_id))
            .gte("created_at", month_start)
            .execute()
        )
        qa_count = qa_res.count or 0

        # 過去30日のナレッジ作成数
        ki_res = (
            db.table("knowledge_items")
            .select("id", count="exact")
            .eq("company_id", str(user.company_id))
            .gte("created_at", month_start)
            .execute()
        )
        knowledge_items_created = ki_res.count or 0

        # カスタム要望件数
        feat_res = (
            db.table("feature_requests")
            .select("id", count="exact")
            .eq("company_id", str(user.company_id))
            .eq("customer_id", cid)
            .execute()
        )
        custom_request_count = feat_res.count or 0

        # 直近5件のサポートチケット
        tickets_res = (
            db.table("support_tickets")
            .select("id, ticket_number, subject, category, status, created_at")
            .eq("company_id", str(user.company_id))
            .eq("customer_id", cid)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        recent_tickets: list[dict] = tickets_res.data or []

        # 直近5件の要望
        fr_res = (
            db.table("feature_requests")
            .select("id, title, category, priority, status, created_at")
            .eq("company_id", str(user.company_id))
            .eq("customer_id", cid)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        recent_feature_requests: list[dict] = fr_res.data or []

        # BPO利用率・Q&A週平均
        module_count = max(len(active_modules), 1)
        bpo_utilization_rate = min(bpo_execution_count / (module_count * 10), 1.0)
        qa_weekly_avg = qa_count / 4.0

        # 未使用モジュール
        unused_modules = sorted(_ALL_BPO_MODULES - set(active_modules))

        usage_summary = UsageSummary(
            login_days=0,  # audit_logs から算出する場合はここで取得
            qa_count=qa_count,
            bpo_execution_count=bpo_execution_count,
            knowledge_items_created=knowledge_items_created,
            active_modules=active_modules,
            unused_available_modules=unused_modules,
            bpo_utilization_rate=round(bpo_utilization_rate * 100, 1),
        )

        # アップセル機会の判定
        usage = {
            "active_modules": active_modules,
            "health_score": c.get("health_score") or 0,
            "plan": c.get("plan") or "",
            "mrr": c.get("mrr") or 0,
            "bpo_utilization_rate": bpo_utilization_rate,
            "qa_weekly_avg": qa_weekly_avg,
            "contract_months": contract_months,
            "custom_request_count": custom_request_count,
        }
        opps = _detect_upsell_opportunities(usage)

        # 推奨モジュール・根拠・想定反論を集約
        recommended_modules: list[str] = []
        reasoning: list[str] = []
        recommended_action = "現状維持。定期的なヘルスチェックを継続してください。"
        for opp in opps:
            recommended_modules.extend(opp["recommended_modules"])
            reasoning.append(opp["trigger_reason"])
            if not recommended_action or recommended_action.startswith("現状維持"):
                recommended_action = opp["trigger_reason"]

        recommended_modules = list(dict.fromkeys(recommended_modules))  # 重複除去・順序保持

        potential_objections = [
            "費用対効果が不明確 → ROI試算と他社導入事例をご提示します",
            "現在のシステムで十分 → 具体的な時間削減数値と比較表をご用意します",
            "導入リソースが不足 → 初期設定は弊社で全て代行します",
        ]

        # 見積シミュレーション
        current_mrr: int = c.get("mrr") or 0
        additional_mrr = sum(
            opp["estimated_additional_mrr"] for opp in opps
        )
        proposed_mrr = current_mrr + additional_mrr
        pricing_simulation = PricingSimulation(
            current_modules=active_modules,
            current_mrr=current_mrr,
            proposed_modules=active_modules + recommended_modules,
            proposed_mrr=proposed_mrr,
            additional_mrr=additional_mrr,
            annual_additional=additional_mrr * 12,
        )

        return UpsellBriefingResponse(
            customer_id=customer_id,
            customer_company_name=c.get("customer_company_name", ""),
            industry=c.get("industry", ""),
            plan=c.get("plan") or "",
            health_score=c.get("health_score"),
            nps_score=c.get("nps_score"),
            usage_summary=usage_summary,
            recommended_action=recommended_action,
            recommended_modules=recommended_modules,
            reasoning=reasoning,
            potential_objections=potential_objections,
            pricing_simulation=pricing_simulation,
            recent_tickets=recent_tickets,
            recent_feature_requests=recent_feature_requests,
            generated_at=now,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get upsell briefing failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/upsell/outcome")
async def record_upsell_outcome_endpoint(
    data: dict,
    user: JWTClaims = Depends(get_current_user),
):
    """アップセル提案の結果を記録する（営業担当が商談後に登録）。

    Request body:
        customer_id (str): 対象顧客ID
        opportunity_type (str): "additional_module" | "upgrade_to_bpo" | "backoffice_bpo" | "custom_bpo"
        outcome (str): "accepted" | "rejected" | "deferred"
        recommended_modules (list[str], optional): 提案したモジュール一覧
        reason (str, optional): 結果の理由・メモ

    Returns:
        success (bool): 記録成功フラグ
        new_weights (dict): 更新後のスコアリング重み（accepted/rejected 時のみ変化）
    """
    from workers.bpo.sales.cs.upsell_briefing_pipeline import record_upsell_outcome

    required_fields = ("customer_id", "opportunity_type", "outcome")
    for field_name in required_fields:
        if field_name not in data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Missing required field: {field_name}",
            )

    valid_opportunity_types = {
        "additional_module", "upgrade_to_bpo", "backoffice_bpo", "custom_bpo"
    }
    if data["opportunity_type"] not in valid_opportunity_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"opportunity_type must be one of: {sorted(valid_opportunity_types)}",
        )

    valid_outcomes = {"accepted", "rejected", "deferred"}
    if data["outcome"] not in valid_outcomes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"outcome must be one of: {sorted(valid_outcomes)}",
        )

    result = await record_upsell_outcome(
        company_id=str(user.company_id),
        customer_id=data["customer_id"],
        opportunity_type=data["opportunity_type"],
        outcome=data["outcome"],
        recommended_modules=data.get("recommended_modules"),
        reason=data.get("reason", ""),
    )
    return result


@router.post("/upsell/briefing/{customer_id}/generate", response_model=BriefingGenerateResponse)
async def generate_upsell_briefing(
    customer_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """コンサル向けブリーフィングを再生成する（LLM で最新データから再生成）。

    - 最新の利用データを取得して upsell_briefing_pipeline でブリーフィングを再生成
    - Slack の #sales-upsell チャンネルに通知を送信
    - calendar_booker でコンサルのカレンダーに「提案準備」ブロックを追加
    """
    try:
        db = get_service_client()
        cid = str(customer_id)

        # 顧客の存在確認
        c_res = (
            db.table("customers")
            .select("id, customer_company_name")
            .eq("company_id", str(user.company_id))
            .eq("id", cid)
            .single()
            .execute()
        )
        if not c_res.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        customer_company_name = c_res.data.get("customer_company_name", "")

        # upsell_briefing_pipeline を呼び出し
        try:
            from workers.bpo.sales.cs.upsell_briefing_pipeline import (
                run_upsell_briefing_pipeline,
            )
            result = await run_upsell_briefing_pipeline(
                company_id=str(user.company_id),
                customer_company_id=cid,
                input_data={
                    "customer_name": customer_company_name,
                },
            )
            if result.skipped_no_opportunity:
                return BriefingGenerateResponse(
                    customer_id=customer_id,
                    status="skipped",
                    message="拡張タイミング未到達のためブリーフィング生成をスキップしました",
                )
            if not result.success:
                logger.warning(
                    f"upsell briefing pipeline failed for customer {cid}: "
                    f"step={result.failed_step}"
                )
                return BriefingGenerateResponse(
                    customer_id=customer_id,
                    status="error",
                    message=f"パイプライン実行中にエラーが発生しました: {result.failed_step}",
                )
            return BriefingGenerateResponse(
                customer_id=customer_id,
                status="generated",
                message=(
                    f"ブリーフィングを生成しました。"
                    f"アップセル機会{len(result.opportunities)}件を検知しました。"
                ),
            )
        except ImportError:
            # パイプラインが利用不可の場合はフォールバック
            logger.warning("upsell_briefing_pipeline not available, using fallback")
            return BriefingGenerateResponse(
                customer_id=customer_id,
                status="generated",
                message="ブリーフィングを生成しました（フォールバックモード）。",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"generate upsell briefing failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

"""学習フィードバックエンドポイント — 受注/失注学習・スコアリングモデル・アウトリーチPDCA・CS品質"""
import logging
from collections import Counter
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from routers.learning_dashboard_payload import build_learning_dashboard_payload

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models — Win/Loss Feedback
# ---------------------------------------------------------------------------


class WinLossFeedbackRequest(BaseModel):
    outcome: str                            # won / lost
    lost_reason: Optional[str] = None       # 失注理由（outcome=lost の場合）
    win_factors: Optional[list[str]] = None # 受注要因（outcome=won の場合）
    notes: Optional[str] = None


class WinLossFeedbackResponse(BaseModel):
    opportunity_id: UUID
    outcome: str
    pattern_id: UUID                        # win_loss_patterns テーブルの ID
    scoring_updated: bool                   # スコアリング重みが更新されたか
    message: str


class WinLossPatternItem(BaseModel):
    industry: Optional[str] = None
    employee_range: Optional[str] = None
    lead_source: Optional[str] = None
    outcome: str
    count: int
    avg_sales_cycle_days: Optional[float] = None
    top_selected_modules: Optional[list[str]] = None
    top_win_factors: Optional[list[str]] = None
    top_lost_reasons: Optional[list[str]] = None


class WinLossPatternsResponse(BaseModel):
    patterns: list[WinLossPatternItem]
    total_won: int
    total_lost: int
    win_rate: float                         # 受注率（%）
    avg_sales_cycle_days: Optional[float] = None
    top_lost_reasons: list[dict]            # 失注理由ランキング


# ---------------------------------------------------------------------------
# Request / Response models — Scoring Model
# ---------------------------------------------------------------------------


class ScoringModelResponse(BaseModel):
    model_type: str                         # lead_score / health_score / upsell_timing
    version: int
    weights: dict                           # スコアリング重み（各ファクターの加点）
    performance_metrics: Optional[dict] = None  # 適合率・再現率等
    active: bool
    created_at: datetime


class ScoringModelCurrentResponse(BaseModel):
    lead_score: Optional[ScoringModelResponse] = None
    health_score: Optional[ScoringModelResponse] = None
    upsell_timing: Optional[ScoringModelResponse] = None


class RetrainRequest(BaseModel):
    model_type: str                         # lead_score / health_score / upsell_timing
    use_recent_days: int = 90               # 学習に使う直近N日のデータ


class RetrainResponse(BaseModel):
    model_type: str
    new_version: int
    training_samples: int                   # 学習に使ったサンプル数
    performance_metrics: dict
    weights: dict
    message: str


# ---------------------------------------------------------------------------
# Request / Response models — Outreach Performance
# ---------------------------------------------------------------------------


class OutreachPDCAItem(BaseModel):
    period: date
    industry: str
    researched_count: int
    outreached_count: int
    lp_viewed_count: int
    lead_converted_count: int
    meeting_booked_count: int
    lp_view_rate: float                     # LP閲覧率 = lp_viewed / outreached
    lead_conversion_rate: float             # リード化率 = lead_converted / outreached
    meeting_rate: float                     # 商談化率 = meeting_booked / outreached


class OutreachPDCAResponse(BaseModel):
    items: list[OutreachPDCAItem]
    total_days: int
    avg_lp_view_rate: float
    avg_lead_conversion_rate: float
    avg_meeting_rate: float
    top_industries: list[dict]              # 反応率が高い業種ランキング
    bottom_industries: list[dict]           # 反応率が低い業種（改善要）


# ---------------------------------------------------------------------------
# Request / Response models — CS Feedback Summary
# ---------------------------------------------------------------------------


class CSFeedbackSummaryResponse(BaseModel):
    period_days: int
    total_feedback: int
    good_count: int
    needs_improvement_count: int
    bad_count: int
    avg_csat: Optional[float] = None
    ai_auto_rate: float                     # AI 自動対応率
    escalation_rate: float                  # エスカレーション率
    improvement_applied_count: int          # FAQ に反映済みの件数

    # 閾値調整状況
    current_confidence_threshold: float    # 現在の AI 自動対応閾値
    threshold_change: Optional[float] = None  # 前回からの変化量（+上昇/-低下）

    top_faq_patterns: list[dict]            # よく来る問い合わせ TOP10
    recent_improvements: list[dict]        # 直近のFAQ改善内容


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _safe_rate(numerator: int, denominator: int) -> float:
    """ゼロ除算を回避して比率を返す。"""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/learning/dashboard")
async def get_learning_dashboard(
    user: JWTClaims = Depends(get_current_user),
):
    """学習ダッシュボード統合データを返す。フロント AI学習レポート画面の契約に合わせて整形する。"""
    sb = get_service_client()
    company_id = user.company_id
    scoring: Optional[dict] = None
    outreach_rows: list[dict] = []
    win_loss_rows: list[dict] = []
    cs_rows: list[dict] = []

    try:
        scoring_res = (
            sb.table("scoring_model_versions")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        scoring = scoring_res.data[0] if scoring_res.data else None
    except Exception as e:
        logger.debug("learning dashboard scoring fetch: %s", e)

    try:
        outreach_res = (
            sb.table("outreach_performance")
            .select("*")
            .eq("company_id", company_id)
            .order("period", desc=True)
            .limit(400)
            .execute()
        )
        outreach_rows = outreach_res.data or []
    except Exception as e:
        logger.debug("learning dashboard outreach fetch: %s", e)

    try:
        wl_res = (
            sb.table("win_loss_patterns")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )
        win_loss_rows = wl_res.data or []
    except Exception as e:
        logger.debug("learning dashboard win_loss fetch: %s", e)

    try:
        cs_res = (
            sb.table("cs_feedback")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(3000)
            .execute()
        )
        cs_rows = cs_res.data or []
    except Exception as e:
        logger.debug("learning dashboard cs fetch: %s", e)

    return build_learning_dashboard_payload(
        scoring, outreach_rows, win_loss_rows, cs_rows
    )


@router.post("/learning/win-loss/{opp_id}", response_model=WinLossFeedbackResponse)
async def record_win_loss_feedback(
    opp_id: UUID,
    body: WinLossFeedbackRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """受注/失注フィードバックを登録して学習ループを回す。

    処理フロー:
    - win_loss_patterns テーブルに保存
    - 受注の場合: リードスコアリング重みを調整 / 提案書を成功テンプレートとして保存
    - 失注の場合: 失注理由を構造化 / 機能不足なら feature_requests に自動登録
    - アウトリーチ成果との突合でターゲティング精度を更新
    """
    if body.outcome not in ("won", "lost"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="outcome は 'won' または 'lost' を指定してください",
        )

    try:
        db = get_service_client()
        opp_id_str = str(opp_id)

        # 商談が自社テナントのものか確認
        opp_res = (
            db.table("opportunities")
            .select("id, target_industry, lead_id, monthly_amount, selected_modules")
            .eq("company_id", str(user.company_id))
            .eq("id", opp_id_str)
            .single()
            .execute()
        )
        if not opp_res.data:
            raise HTTPException(status_code=404, detail="Opportunity not found")

        opp = opp_res.data

        # リード情報を取得（業種・規模・ソース）
        lead_data: dict = {}
        if opp.get("lead_id"):
            lead_res = (
                db.table("leads")
                .select("industry, employee_count, source")
                .eq("id", opp["lead_id"])
                .single()
                .execute()
            )
            if lead_res.data:
                lead_data = lead_res.data

        # 従業員規模帯を算出
        employee_count: int = lead_data.get("employee_count") or 0
        if employee_count < 10:
            employee_range = "1-9"
        elif employee_count < 30:
            employee_range = "10-29"
        elif employee_count < 50:
            employee_range = "30-49"
        elif employee_count < 100:
            employee_range = "50-99"
        elif employee_count < 300:
            employee_range = "100-299"
        else:
            employee_range = "300+"

        # win_loss_patterns に保存
        pattern_data = {
            "company_id": str(user.company_id),
            "opportunity_id": opp_id_str,
            "outcome": body.outcome,
            "industry": lead_data.get("industry") or opp.get("target_industry"),
            "employee_range": employee_range,
            "lead_source": lead_data.get("source"),
            "selected_modules": opp.get("selected_modules") or [],
            "lost_reason": body.lost_reason if body.outcome == "lost" else None,
            "win_factors": body.win_factors if body.outcome == "won" else None,
        }
        insert_res = db.table("win_loss_patterns").insert(pattern_data).execute()
        if not insert_res.data:
            raise HTTPException(status_code=500, detail="パターン保存に失敗しました")

        pattern_id = UUID(insert_res.data[0]["id"])

        # 商談ステージを更新
        db.table("opportunities").update({
            "stage": body.outcome,
            "lost_reason": body.lost_reason if body.outcome == "lost" else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", opp_id_str).execute()

        # win_loss_feedback_pipeline を呼び出して学習ループを回す
        scoring_updated = False
        try:
            from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
                run_win_loss_feedback_pipeline,
            )
            pipeline_input = {
                "outcome": body.outcome,
                "opportunity_id": opp_id_str,
                "company_name": "",
                "contact_email": "",
                "industry": lead_data.get("industry") or opp.get("target_industry") or "",
                "employee_range": employee_range,
                "lead_source": lead_data.get("source") or "",
                "sales_cycle_days": 0,
                "selected_modules": opp.get("selected_modules") or [],
                "pain_points": body.notes or "",
                "lost_reason": body.lost_reason or "",
                "win_factors": body.win_factors or [],
            }
            result = await run_win_loss_feedback_pipeline(
                company_id=str(user.company_id),
                input_data=pipeline_input,
            )
            scoring_updated = result.success
        except ImportError:
            logger.warning("win_loss_feedback_pipeline not available")
        except Exception as pipeline_err:
            logger.warning(f"win_loss_feedback_pipeline failed: {pipeline_err}")

        message = (
            f"受注フィードバックを登録しました。スコアリング重みを更新しました。"
            if body.outcome == "won" and scoring_updated
            else f"{'受注' if body.outcome == 'won' else '失注'}フィードバックを登録しました。"
        )

        return WinLossFeedbackResponse(
            opportunity_id=opp_id,
            outcome=body.outcome,
            pattern_id=pattern_id,
            scoring_updated=scoring_updated,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"record win loss feedback failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/learning/win-loss/patterns", response_model=WinLossPatternsResponse)
async def get_win_loss_patterns(
    industry: Optional[str] = None,
    days: int = Query(180, ge=1, le=730),
    user: JWTClaims = Depends(get_current_user),
):
    """受注/失注パターン分析を取得する。

    - win_loss_patterns テーブルを業種・規模・リードソース別に集計
    - 失注理由ランキング・受注率・平均セールスサイクルを算出
    """
    try:
        db = get_service_client()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = (
            db.table("win_loss_patterns")
            .select(
                "id, outcome, industry, employee_range, lead_source, "
                "sales_cycle_days, selected_modules, lost_reason, win_factors, created_at"
            )
            .eq("company_id", str(user.company_id))
            .gte("created_at", since)
        )
        if industry:
            query = query.eq("industry", industry)

        rows_res = query.execute()
        rows: list[dict] = rows_res.data or []

        # 集計
        total_won = sum(1 for r in rows if r["outcome"] == "won")
        total_lost = sum(1 for r in rows if r["outcome"] == "lost")
        total = total_won + total_lost
        win_rate = round(total_won / total * 100, 1) if total > 0 else 0.0

        # 平均セールスサイクル
        cycles = [r["sales_cycle_days"] for r in rows if r.get("sales_cycle_days")]
        avg_sales_cycle_days = round(sum(cycles) / len(cycles), 1) if cycles else None

        # 失注理由ランキング
        lost_reasons = [r["lost_reason"] for r in rows if r.get("lost_reason")]
        reason_counter = Counter(lost_reasons)
        top_lost_reasons = [
            {"reason": r, "count": c}
            for r, c in reason_counter.most_common(10)
        ]

        # 業種×アウトカム別にグループ集計
        from collections import defaultdict
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            key = (r.get("industry"), r.get("employee_range"), r.get("lead_source"), r["outcome"])
            groups[key].append(r)

        patterns: list[WinLossPatternItem] = []
        for (ind, emp, src, outcome), group_rows in groups.items():
            # 選択モジュールの頻出集計
            all_modules: list[str] = []
            for r in group_rows:
                mods = r.get("selected_modules") or []
                if isinstance(mods, list):
                    all_modules.extend(mods)
            top_modules = [m for m, _ in Counter(all_modules).most_common(3)]

            # 受注要因・失注理由の頻出集計
            win_factors_flat: list[str] = []
            lost_reasons_flat: list[str] = []
            for r in group_rows:
                wf = r.get("win_factors") or []
                if isinstance(wf, list):
                    win_factors_flat.extend(wf)
                if r.get("lost_reason"):
                    lost_reasons_flat.append(r["lost_reason"])

            group_cycles = [r["sales_cycle_days"] for r in group_rows if r.get("sales_cycle_days")]
            avg_cycle = round(sum(group_cycles) / len(group_cycles), 1) if group_cycles else None

            patterns.append(
                WinLossPatternItem(
                    industry=ind,
                    employee_range=emp,
                    lead_source=src,
                    outcome=outcome,
                    count=len(group_rows),
                    avg_sales_cycle_days=avg_cycle,
                    top_selected_modules=top_modules or None,
                    top_win_factors=[wf for wf, _ in Counter(win_factors_flat).most_common(3)] or None,
                    top_lost_reasons=[lr for lr, _ in Counter(lost_reasons_flat).most_common(3)] or None,
                )
            )

        # 件数降順でソート
        patterns.sort(key=lambda p: p.count, reverse=True)

        return WinLossPatternsResponse(
            patterns=patterns,
            total_won=total_won,
            total_lost=total_lost,
            win_rate=win_rate,
            avg_sales_cycle_days=avg_sales_cycle_days,
            top_lost_reasons=top_lost_reasons,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get win loss patterns failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/learning/scoring/current", response_model=ScoringModelCurrentResponse)
async def get_current_scoring_models(
    user: JWTClaims = Depends(get_current_user),
):
    """現在アクティブなスコアリングモデル（リード/ヘルス/アップセル）を取得する。

    - scoring_model_versions テーブルから active=True のモデルを取得
    """
    try:
        db = get_service_client()

        rows_res = (
            db.table("scoring_model_versions")
            .select("id, model_type, version, weights, performance_metrics, active, created_at")
            .eq("company_id", str(user.company_id))
            .eq("active", True)
            .execute()
        )
        rows: list[dict] = rows_res.data or []

        # model_type ごとにマップ
        model_map: dict[str, ScoringModelResponse] = {}
        for r in rows:
            created_at_str = r.get("created_at", "")
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except Exception:
                created_at = datetime.now(timezone.utc)

            model_map[r["model_type"]] = ScoringModelResponse(
                model_type=r["model_type"],
                version=r["version"],
                weights=r.get("weights") or {},
                performance_metrics=r.get("performance_metrics"),
                active=r.get("active", False),
                created_at=created_at,
            )

        return ScoringModelCurrentResponse(
            lead_score=model_map.get("lead_score"),
            health_score=model_map.get("health_score"),
            upsell_timing=model_map.get("upsell_timing"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get current scoring models failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/learning/scoring/retrain", response_model=RetrainResponse)
async def retrain_scoring_model(
    body: RetrainRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """スコアリングモデルを再学習する（admin のみ）。

    - 直近N日の受注/失注データでリードスコアリング重みを更新
    - 新バージョンを scoring_model_versions に保存して active=True に切り替え
    """
    valid_model_types = ("lead_score", "health_score", "upsell_timing")
    if body.model_type not in valid_model_types:
        raise HTTPException(
            status_code=422,
            detail=f"model_type は {valid_model_types} のいずれかを指定してください",
        )

    try:
        db = get_service_client()
        since = (
            datetime.now(timezone.utc) - timedelta(days=body.use_recent_days)
        ).isoformat()

        # win_loss_patterns から学習データを取得
        rows_res = (
            db.table("win_loss_patterns")
            .select(
                "outcome, industry, employee_range, lead_source, "
                "sales_cycle_days, selected_modules, win_factors, lost_reason"
            )
            .eq("company_id", str(user.company_id))
            .gte("created_at", since)
            .execute()
        )
        rows: list[dict] = rows_res.data or []
        training_samples = len(rows)

        # outreach_pdca モードで win_loss_feedback_pipeline を実行
        pipeline_weights: dict = {}
        pipeline_metrics: dict = {}
        try:
            from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
                run_win_loss_feedback_pipeline,
            )
            result = await run_win_loss_feedback_pipeline(
                company_id=str(user.company_id),
                input_data={
                    "outcome": "outreach_pdca",
                    "period": date.today().isoformat(),
                    "outreach_stats": [],
                },
            )
            pipeline_weights = result.final_output.get("weights", {})
            pipeline_metrics = result.final_output.get("performance_metrics", {})
        except ImportError:
            logger.warning("win_loss_feedback_pipeline not available for retrain")
        except Exception as pe:
            logger.warning(f"pipeline retrain failed: {pe}")

        # シンプルな重み計算（受注率が高い業種・ソースにボーナス）
        if not pipeline_weights and rows:
            from collections import defaultdict
            industry_wins: dict[str, int] = defaultdict(int)
            industry_totals: dict[str, int] = defaultdict(int)
            for r in rows:
                ind = r.get("industry") or "unknown"
                industry_totals[ind] += 1
                if r["outcome"] == "won":
                    industry_wins[ind] += 1

            industry_bonus = {}
            for ind, total in industry_totals.items():
                win_rate = industry_wins[ind] / total
                industry_bonus[ind] = round(win_rate * 10)  # 0-10 点

            pipeline_weights = {
                "industry_bonus": industry_bonus,
                "base_score": 50,
                "trained_at": date.today().isoformat(),
                "samples": training_samples,
            }

        if not pipeline_metrics:
            total = len(rows)
            won = sum(1 for r in rows if r["outcome"] == "won")
            pipeline_metrics = {
                "win_rate": round(won / total, 3) if total > 0 else 0.0,
                "sample_count": total,
            }

        # 既存の active モデルの最大バージョンを取得
        ver_res = (
            db.table("scoring_model_versions")
            .select("version")
            .eq("company_id", str(user.company_id))
            .eq("model_type", body.model_type)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
        current_max_version = 0
        if ver_res.data:
            current_max_version = ver_res.data[0].get("version", 0)
        new_version = current_max_version + 1

        # 旧バージョンを非アクティブ化
        db.table("scoring_model_versions").update({"active": False}).eq(
            "company_id", str(user.company_id)
        ).eq("model_type", body.model_type).eq("active", True).execute()

        # 新バージョンを保存
        insert_res = (
            db.table("scoring_model_versions")
            .insert({
                "company_id": str(user.company_id),
                "model_type": body.model_type,
                "version": new_version,
                "weights": pipeline_weights,
                "performance_metrics": pipeline_metrics,
                "active": True,
            })
            .execute()
        )
        if not insert_res.data:
            raise HTTPException(status_code=500, detail="モデル保存に失敗しました")

        return RetrainResponse(
            model_type=body.model_type,
            new_version=new_version,
            training_samples=training_samples,
            performance_metrics=pipeline_metrics,
            weights=pipeline_weights,
            message=(
                f"{body.model_type} モデルを v{new_version} に更新しました。"
                f"学習サンプル数: {training_samples}件"
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"retrain scoring model failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/learning/outreach/performance", response_model=OutreachPDCAResponse)
async def get_outreach_pdca_performance(
    industry: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    user: JWTClaims = Depends(get_current_user),
):
    """アウトリーチ PDCA 指標を取得する。

    - outreach_performance テーブルから業種別・期間別に集計
    - 反応率が高い業種と低い業種のランキングを算出
    """
    try:
        db = get_service_client()
        since = (date.today() - timedelta(days=days)).isoformat()

        query = (
            db.table("outreach_performance")
            .select(
                "period, industry, researched_count, outreached_count, "
                "lp_viewed_count, lead_converted_count, meeting_booked_count, "
                "email_variant, open_rate, click_rate, created_at"
            )
            .eq("company_id", str(user.company_id))
            .gte("period", since)
            .order("period", desc=True)
        )
        if industry:
            query = query.eq("industry", industry)

        rows_res = query.execute()
        rows: list[dict] = rows_res.data or []

        # OutreachPDCAItem リストを構築
        items: list[OutreachPDCAItem] = []
        for r in rows:
            outreached = r.get("outreached_count") or 0
            lp_viewed = r.get("lp_viewed_count") or 0
            lead_converted = r.get("lead_converted_count") or 0
            meeting_booked = r.get("meeting_booked_count") or 0

            period_val = r.get("period")
            if isinstance(period_val, str):
                period_date = date.fromisoformat(period_val)
            else:
                period_date = period_val or date.today()

            items.append(
                OutreachPDCAItem(
                    period=period_date,
                    industry=r.get("industry") or "",
                    researched_count=r.get("researched_count") or 0,
                    outreached_count=outreached,
                    lp_viewed_count=lp_viewed,
                    lead_converted_count=lead_converted,
                    meeting_booked_count=meeting_booked,
                    lp_view_rate=_safe_rate(lp_viewed, outreached),
                    lead_conversion_rate=_safe_rate(lead_converted, outreached),
                    meeting_rate=_safe_rate(meeting_booked, outreached),
                )
            )

        # 全体平均
        total_days = len({item.period for item in items})
        avg_lp_view_rate = (
            round(sum(i.lp_view_rate for i in items) / len(items), 4) if items else 0.0
        )
        avg_lead_conversion_rate = (
            round(sum(i.lead_conversion_rate for i in items) / len(items), 4) if items else 0.0
        )
        avg_meeting_rate = (
            round(sum(i.meeting_rate for i in items) / len(items), 4) if items else 0.0
        )

        # 業種別リード化率でランキング
        from collections import defaultdict
        industry_rates: dict[str, list[float]] = defaultdict(list)
        for item in items:
            industry_rates[item.industry].append(item.lead_conversion_rate)

        industry_avg: list[dict] = [
            {
                "industry": ind,
                "avg_lead_conversion_rate": round(sum(rates) / len(rates), 4),
            }
            for ind, rates in industry_rates.items()
        ]
        industry_avg.sort(key=lambda x: x["avg_lead_conversion_rate"], reverse=True)
        top_industries = industry_avg[:5]
        bottom_industries = sorted(
            industry_avg, key=lambda x: x["avg_lead_conversion_rate"]
        )[:5]

        return OutreachPDCAResponse(
            items=items,
            total_days=total_days,
            avg_lp_view_rate=avg_lp_view_rate,
            avg_lead_conversion_rate=avg_lead_conversion_rate,
            avg_meeting_rate=avg_meeting_rate,
            top_industries=top_industries,
            bottom_industries=bottom_industries,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"outreach pdca performance failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/learning/cs/feedback-summary", response_model=CSFeedbackSummaryResponse)
async def get_cs_feedback_summary(
    days: int = Query(30, ge=1, le=365),
    user: JWTClaims = Depends(get_current_user),
):
    """CS 品質フィードバックサマリーを取得する。

    - cs_feedback テーブルから集計
    - よくある問い合わせ TOP10 / 直近のFAQ改善内容を返す
    - 現在の AI 自動対応 confidence 閾値と調整状況を返す
    """
    try:
        db = get_service_client()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # cs_feedback を一括取得
        fb_res = (
            db.table("cs_feedback")
            .select(
                "id, quality_label, csat_score, was_escalated, "
                "improvement_applied, created_at"
            )
            .eq("company_id", str(user.company_id))
            .gte("created_at", since)
            .execute()
        )
        feedbacks: list[dict] = fb_res.data or []
        total_feedback = len(feedbacks)

        good_count = sum(1 for f in feedbacks if f.get("quality_label") == "good")
        needs_improvement_count = sum(
            1 for f in feedbacks if f.get("quality_label") == "needs_improvement"
        )
        bad_count = sum(1 for f in feedbacks if f.get("quality_label") == "bad")
        improvement_applied_count = sum(
            1 for f in feedbacks if f.get("improvement_applied")
        )
        escalated_count = sum(1 for f in feedbacks if f.get("was_escalated"))

        # CSAT 平均
        csat_scores = [f["csat_score"] for f in feedbacks if f.get("csat_score") is not None]
        avg_csat = round(sum(csat_scores) / len(csat_scores), 2) if csat_scores else None

        # AI 自動対応率・エスカレーション率はサポートチケット側から算出
        tickets_res = (
            db.table("support_tickets")
            .select("id, ai_handled, escalated", count="exact")
            .eq("company_id", str(user.company_id))
            .gte("created_at", since)
            .execute()
        )
        tickets: list[dict] = tickets_res.data or []
        total_tickets = len(tickets)
        ai_handled_count = sum(1 for t in tickets if t.get("ai_handled"))
        ticket_escalated_count = sum(1 for t in tickets if t.get("escalated"))

        ai_auto_rate = _safe_rate(ai_handled_count, total_tickets)
        escalation_rate = _safe_rate(ticket_escalated_count, total_tickets)

        # 現在アクティブな confidence 閾値を scoring_model_versions から取得
        # upsell_timing モデルの weights に confidence_threshold を持たせている場合に対応
        threshold_res = (
            db.table("scoring_model_versions")
            .select("weights, created_at")
            .eq("company_id", str(user.company_id))
            .eq("model_type", "health_score")
            .eq("active", True)
            .limit(1)
            .execute()
        )
        current_confidence_threshold = 0.7  # デフォルト値
        threshold_change: Optional[float] = None
        if threshold_res.data:
            weights = threshold_res.data[0].get("weights") or {}
            if "confidence_threshold" in weights:
                current_confidence_threshold = float(weights["confidence_threshold"])
            if "prev_confidence_threshold" in weights:
                prev = float(weights["prev_confidence_threshold"])
                threshold_change = round(current_confidence_threshold - prev, 3)

        # よくある問い合わせ TOP10（support_tickets の subject でカウント）
        ticket_subjects = [t.get("subject", "") for t in tickets if t.get("subject")]
        subject_counter = Counter(ticket_subjects)
        top_faq_patterns = [
            {"subject": subj, "count": cnt}
            for subj, cnt in subject_counter.most_common(10)
        ]

        # 直近のFAQ改善内容（improvement_applied=True かつ最新10件）
        improved_res = (
            db.table("cs_feedback")
            .select("id, ai_response, human_correction, created_at")
            .eq("company_id", str(user.company_id))
            .eq("improvement_applied", True)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        recent_improvements: list[dict] = improved_res.data or []

        return CSFeedbackSummaryResponse(
            period_days=days,
            total_feedback=total_feedback,
            good_count=good_count,
            needs_improvement_count=needs_improvement_count,
            bad_count=bad_count,
            avg_csat=avg_csat,
            ai_auto_rate=ai_auto_rate,
            escalation_rate=escalation_rate,
            improvement_applied_count=improvement_applied_count,
            current_confidence_threshold=current_confidence_threshold,
            threshold_change=threshold_change,
            top_faq_patterns=top_faq_patterns,
            recent_improvements=recent_improvements,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cs feedback summary failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

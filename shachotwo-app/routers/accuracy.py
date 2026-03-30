"""精度モニタリング・グレースフルデグラデーション・自動改善エンドポイント。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.inference.accuracy_monitor import get_accuracy_report
from brain.inference.improvement_cycle import run_improvement_cycle
from db.supabase import get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# レスポンスモデル
# ─────────────────────────────────────

class ImproveCycleRequest(BaseModel):
    dry_run: bool = True
    confidence_threshold: float = 0.75
    days: int = 7


class PipelineAccuracy(BaseModel):
    pipeline_name: str
    confidence: float           # 0.0-1.0 直近30件の平均精度
    data_completeness: float    # 0.0-1.0 データ充足度
    total_executions: int
    approved_count: int
    rejected_count: int
    accuracy_trend: str         # "improving" | "stable" | "declining"
    recommendations: list[str]  # 精度向上のための推奨アクション


class DataCompletenessDetail(BaseModel):
    category: str
    current_count: int
    recommended_count: int
    completeness: float


# ─────────────────────────────────────
# 業種別カテゴリ定義（data_completeness 計算用）
# ─────────────────────────────────────

# pipeline_name -> {category: recommended_count}
_PIPELINE_CATEGORY_REQUIREMENTS: dict[str, dict[str, int]] = {
    "manufacturing": {
        "製品": 10,
        "設備": 10,
        "仕入先": 10,
        "品質": 10,
        "原価": 10,
    },
    "construction": {
        "工種": 10,
        "資材": 10,
        "下請業者": 10,
        "安全": 10,
        "原価": 10,
    },
    "realestate": {
        "物件": 10,
        "契約": 10,
        "家賃": 10,
        "修繕": 10,
        "顧客": 10,
    },
    "logistics": {
        "配車": 10,
        "荷主": 10,
        "ドライバー": 10,
        "ルート": 10,
        "運賃": 10,
    },
    "wholesale": {
        "商品": 10,
        "仕入先": 10,
        "得意先": 10,
        "在庫": 10,
        "価格": 10,
    },
    "medical": {
        "診療": 10,
        "患者": 10,
        "薬剤": 10,
        "請求": 10,
        "スタッフ": 10,
    },
    "common": {
        "経費": 5,
        "給与": 5,
        "契約": 5,
        "勤怠": 5,
        "取引先": 5,
    },
}

# パイプラインキー（"construction/estimation"）からベース業種を抽出する
def _pipeline_to_industry(pipeline_name: str) -> str:
    return pipeline_name.split("/")[0] if "/" in pipeline_name else pipeline_name


def _calculate_data_completeness(
    db,
    company_id: str,
    pipeline_name: str,
) -> float:
    """knowledge_items のカテゴリ別件数をもとにデータ充足度 (0.0-1.0) を計算する。"""
    industry = _pipeline_to_industry(pipeline_name)
    requirements = _PIPELINE_CATEGORY_REQUIREMENTS.get(industry)
    if not requirements:
        # 定義のない業種はデフォルト 1.0（充足とみなす）
        return 1.0

    total_required = sum(requirements.values())
    if total_required == 0:
        return 1.0

    try:
        result = (
            db.table("knowledge_items")
            .select("category", count="exact")
            .eq("company_id", company_id)
            .eq("is_active", True)
            .execute()
        )
        rows = result.data or []
    except Exception:
        return 0.0

    # カテゴリ別カウント集計
    category_counts: dict[str, int] = {}
    for row in rows:
        cat = row.get("category") or "未分類"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    filled = 0
    for cat, req_count in requirements.items():
        actual = category_counts.get(cat, 0)
        filled += min(actual, req_count)

    return round(min(filled / total_required, 1.0), 4)


def _build_recommendations(
    confidence: float,
    data_completeness: float,
    pipeline_name: str,
    rejected_count: int,
    total_executions: int,
) -> list[str]:
    """状態に応じた推奨アクションリストを生成する。"""
    recs: list[str] = []

    if data_completeness < 0.3:
        recs.append("ナレッジページからCSVまたは手動入力でデータを追加してください（充足度が30%未満です）")
    elif data_completeness < 0.5:
        recs.append("データ充足度が低いため、CSVでデータを一括追加することを推奨します")
    elif data_completeness < 0.8:
        recs.append("各カテゴリのデータ件数を増やすと精度が向上します")

    if confidence < 0.6:
        recs.append("AI精度が低い状態です。代表的な業務例をナレッジに追加してください")
    elif confidence < 0.75:
        recs.append("精度向上のため、過去の業務データをナレッジに登録してください")

    if total_executions > 0 and rejected_count / total_executions > 0.3:
        recs.append("却下率が30%を超えています。フィードバックコメントを確認して改善してください")

    if total_executions < 5:
        recs.append("実行回数が少ないため、まずパイプラインを数回実行してデータを蓄積してください")

    if not recs:
        recs.append("現在の精度は良好です。引き続き定期的なデータ更新を継続してください")

    return recs


# ─────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────

@router.get("/accuracy/report")
async def accuracy_report(
    days: int = 7,
    user: JWTClaims = Depends(get_current_user),
):
    """ステップ別精度レポートを返す（既存エンドポイント）。"""
    reports = await get_accuracy_report(company_id=str(user.company_id), days=days)
    return {"reports": [r.__dict__ for r in reports], "days": days}


@router.post("/accuracy/improve")
async def trigger_improvement(
    body: ImproveCycleRequest,
    user: JWTClaims = Depends(require_role("admin")),
):
    """精度向上サイクルを手動トリガー（admin のみ）。"""
    result = await run_improvement_cycle(
        company_id=str(user.company_id),
        confidence_threshold=body.confidence_threshold,
        dry_run=body.dry_run,
        days=body.days,
    )
    return result


@router.get("/accuracy/pipelines", response_model=list[PipelineAccuracy])
async def get_pipeline_accuracies(
    user: JWTClaims = Depends(get_current_user),
):
    """全パイプラインの精度情報を返す。グレースフルデグラデーション表示用。

    - confidence: execution_logs の直近30件の平均 confidence
    - data_completeness: knowledge_items のカテゴリ別件数をチェック
    - accuracy_trend: 直近7日 vs 前7日の confidence 比較
    - recommendations: 状態に応じた推奨アクション
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)

        # execution_logs から直近30件（パイプライン別）を取得
        result = (
            db.table("execution_logs")
            .select("operations, overall_success, created_at")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(300)  # 全パイプライン合計で最大300件取得してから絞る
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"get_pipeline_accuracies DB取得失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # パイプライン別に集計
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)
    prev_cutoff = now - timedelta(days=14)

    pipeline_data: dict[str, dict] = {}

    for row in rows:
        ops = row.get("operations") or {}
        pipeline = ops.get("pipeline")
        if not pipeline:
            continue

        if pipeline not in pipeline_data:
            pipeline_data[pipeline] = {
                "confidences_recent": [],   # 直近7日
                "confidences_prev": [],     # 前7日
                "confidences_30": [],       # 直近30件用
                "approved": 0,
                "rejected": 0,
                "total": 0,
            }

        pd = pipeline_data[pipeline]
        steps = ops.get("steps") or []
        confs = [float(s["confidence"]) for s in steps if s.get("confidence") is not None]
        avg_conf = sum(confs) / len(confs) if confs else None

        # feedback_detail から approved/rejected カウント
        feedback = ops.get("feedback_detail", {})
        if feedback.get("overall_approved") is True:
            pd["approved"] += 1
        elif feedback.get("overall_approved") is False:
            pd["rejected"] += 1
        # feedback なしで overall_success=True の場合も approved に加算
        elif row.get("overall_success") is True and not feedback:
            pd["approved"] += 1

        pd["total"] += 1

        if avg_conf is not None:
            # 直近30件
            if len(pd["confidences_30"]) < 30:
                pd["confidences_30"].append(avg_conf)

            # 時系列トレンド用
            created_raw = row.get("created_at")
            if created_raw:
                try:
                    created_dt = datetime.fromisoformat(
                        created_raw.replace("Z", "+00:00")
                    )
                    if created_dt >= recent_cutoff:
                        pd["confidences_recent"].append(avg_conf)
                    elif created_dt >= prev_cutoff:
                        pd["confidences_prev"].append(avg_conf)
                except Exception:
                    pass

    # PipelineAccuracy リストを構築
    accuracies: list[PipelineAccuracy] = []

    for pipeline_name, pd in pipeline_data.items():
        confs_30 = pd["confidences_30"]
        confidence = round(sum(confs_30) / len(confs_30), 4) if confs_30 else 0.0

        # accuracy_trend
        recent_avg = (
            sum(pd["confidences_recent"]) / len(pd["confidences_recent"])
            if pd["confidences_recent"] else None
        )
        prev_avg = (
            sum(pd["confidences_prev"]) / len(pd["confidences_prev"])
            if pd["confidences_prev"] else None
        )
        if recent_avg is not None and prev_avg is not None:
            diff = recent_avg - prev_avg
            if diff > 0.03:
                trend = "improving"
            elif diff < -0.03:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # データ充足度
        data_completeness = _calculate_data_completeness(db, company_id, pipeline_name)

        recommendations = _build_recommendations(
            confidence=confidence,
            data_completeness=data_completeness,
            pipeline_name=pipeline_name,
            rejected_count=pd["rejected"],
            total_executions=pd["total"],
        )

        accuracies.append(PipelineAccuracy(
            pipeline_name=pipeline_name,
            confidence=confidence,
            data_completeness=data_completeness,
            total_executions=pd["total"],
            approved_count=pd["approved"],
            rejected_count=pd["rejected"],
            accuracy_trend=trend,
            recommendations=recommendations,
        ))

    # confidence 昇順で返す（精度の低いものを先頭に）
    accuracies.sort(key=lambda x: x.confidence)
    return accuracies


@router.get("/accuracy/data-completeness", response_model=list[DataCompletenessDetail])
async def get_data_completeness(
    user: JWTClaims = Depends(get_current_user),
):
    """カテゴリ別のデータ充足度を返す。

    全業種のカテゴリ要件に対して、現在の knowledge_items 件数を照合する。
    """
    try:
        db = get_service_client()
        company_id = str(user.company_id)

        # カテゴリ別カウントを取得
        result = (
            db.table("knowledge_items")
            .select("category")
            .eq("company_id", company_id)
            .eq("is_active", True)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.error(f"get_data_completeness DB取得失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # カテゴリ別カウント集計
    category_counts: dict[str, int] = {}
    for row in rows:
        cat = row.get("category") or "未分類"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # 全業種の要件を展開して DataCompletenessDetail リストを作成
    seen: set[str] = set()
    details: list[DataCompletenessDetail] = []

    for industry, requirements in _PIPELINE_CATEGORY_REQUIREMENTS.items():
        for category, recommended_count in requirements.items():
            key = f"{industry}:{category}"
            if key in seen:
                continue
            seen.add(key)

            current_count = category_counts.get(category, 0)
            completeness = round(
                min(current_count / recommended_count, 1.0), 4
            ) if recommended_count > 0 else 1.0

            details.append(DataCompletenessDetail(
                category=f"{industry}/{category}",
                current_count=current_count,
                recommended_count=recommended_count,
                completeness=completeness,
            ))

    # completeness 昇順（不足しているものを先頭に）
    details.sort(key=lambda x: x.completeness)
    return details

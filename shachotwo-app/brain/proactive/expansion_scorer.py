"""Expansion Scorer — 顧客の次のステップをスコアリングしてExpand提案を生成する。

REQ-1504: Land and Expand
利用パターンを分析し、次に使うべき機能を最大3件提案する。
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# ステージ判定閾値
_STAGE_ONBOARDING_MAX = 10
_STAGE_ACTIVE_SINGLE_MAX = 50
_STAGE_ACTIVE_MULTI_THRESHOLD = 2   # パイプライン種別数
_STAGE_POWER_USER_MIN = 200

# 業種ペア: 一方を使っていれば他方を提案
_INDUSTRY_PAIRS = [
    ("construction", "manufacturing"),
    ("manufacturing", "construction"),
    ("medical", "professional_social_worker"),
    ("professional_social_worker", "medical"),
    ("real_estate", "construction"),
    ("logistics", "manufacturing"),
    ("wholesale", "manufacturing"),
]

# 各機能の action_url
_PIPELINE_URLS: dict[str, str] = {
    "construction": "/bpo/construction",
    "manufacturing": "/bpo/manufacturing",
    "medical": "/bpo/medical",
    "real_estate": "/bpo/real_estate",
    "logistics": "/bpo/logistics",
    "wholesale": "/bpo/wholesale",
    "professional_social_worker": "/bpo/professional",
}

_PIPELINE_LABELS: dict[str, str] = {
    "construction": "建設業務自動化",
    "manufacturing": "製造業務自動化",
    "medical": "医療・福祉業務自動化",
    "real_estate": "不動産業務自動化",
    "logistics": "物流業務自動化",
    "wholesale": "卸売業務自動化",
    "professional_social_worker": "社労士業務自動化",
}


@dataclass
class NextStep:
    feature: str          # 「製造業務自動化」「マネーフォワード連携」等
    reason: str           # なぜ今これを提案するか
    expected_benefit: str # 「見積→原価→請求が自動化」等
    action_url: str       # クリック先URL
    priority: int         # 1=最高優先


@dataclass
class ExpansionResult:
    company_id: str
    current_stage: str    # "onboarding" | "active_single" | "active_multi" | "power_user"
    usage_score: float    # 0.0-1.0（現在機能の使い込み度）
    next_steps: list[NextStep] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ExpansionScorer:
    """利用パターンを分析してExpand提案を生成する。"""

    async def score(self, company_id: str) -> ExpansionResult:
        """利用パターンを分析し、次のステップをスコアリングして返す。

        Args:
            company_id: 対象企業ID

        Returns:
            ExpansionResult: ステージ情報と次のステップ提案（最大3件）
        """
        db = get_service_client()

        # 今月の期間
        now = datetime.now(timezone.utc)
        period_month = now.strftime("%Y-%m")

        # usage_metrics から今月の pipeline_run を集計
        try:
            metrics_result = db.table("usage_metrics") \
                .select("pipeline_name, quantity") \
                .eq("company_id", company_id) \
                .eq("metric_type", "pipeline_run") \
                .eq("period_month", period_month) \
                .execute()
            metrics = metrics_result.data or []
        except Exception as e:
            logger.warning(
                f"expansion_scorer: failed to fetch usage_metrics for "
                f"company_id={company_id}: {e}. Treating as onboarding."
            )
            metrics = []

        # パイプライン別集計
        pipeline_counts: dict[str, int] = {}
        total_runs = 0
        for row in metrics:
            pname = row.get("pipeline_name") or "unknown"
            qty = row.get("quantity") or 1
            pipeline_counts[pname] = pipeline_counts.get(pname, 0) + qty
            total_runs += qty

        # コネクタ設定を確認（watch_channelsでGWS連携状態を確認）
        gws_connected = False
        try:
            gws_result = db.table("watch_channels") \
                .select("id", count="exact") \
                .eq("company_id", company_id) \
                .eq("is_active", True) \
                .execute()
            gws_connected = (gws_result.count or 0) > 0
        except Exception as e:
            logger.debug(f"expansion_scorer: watch_channels check failed: {e}")

        # ステージ判定
        pipeline_types = {k for k, v in pipeline_counts.items() if v > 0}
        current_stage = _determine_stage(total_runs, pipeline_types)

        # 使い込みスコア (0.0-1.0)
        usage_score = _compute_usage_score(total_runs, current_stage)

        # 次のステップ提案を生成（最大3件）
        next_steps = _build_next_steps(
            current_stage=current_stage,
            pipeline_counts=pipeline_counts,
            pipeline_types=pipeline_types,
            total_runs=total_runs,
            gws_connected=gws_connected,
        )

        return ExpansionResult(
            company_id=company_id,
            current_stage=current_stage,
            usage_score=usage_score,
            next_steps=next_steps[:3],
        )


def _determine_stage(total_runs: int, pipeline_types: set[str]) -> str:
    """利用パターンからステージを判定する。"""
    if total_runs == 0:
        return "onboarding"
    if total_runs < _STAGE_ONBOARDING_MAX:
        return "onboarding"
    if total_runs > _STAGE_POWER_USER_MIN:
        return "power_user"
    if len(pipeline_types) >= _STAGE_ACTIVE_MULTI_THRESHOLD:
        return "active_multi"
    return "active_single"


def _compute_usage_score(total_runs: int, stage: str) -> float:
    """現在機能の使い込み度スコア（0.0-1.0）を算出する。"""
    if stage == "onboarding":
        return min(total_runs / _STAGE_ONBOARDING_MAX, 1.0) * 0.3
    if stage == "active_single":
        normalized = (total_runs - _STAGE_ONBOARDING_MAX) / (
            _STAGE_ACTIVE_SINGLE_MAX - _STAGE_ONBOARDING_MAX
        )
        return 0.3 + min(normalized, 1.0) * 0.4
    if stage == "active_multi":
        normalized = min(total_runs / _STAGE_POWER_USER_MIN, 1.0)
        return 0.7 + normalized * 0.2
    # power_user
    return min(0.9 + total_runs / 2000, 1.0)


def _build_next_steps(
    current_stage: str,
    pipeline_counts: dict[str, int],
    pipeline_types: set[str],
    total_runs: int,
    gws_connected: bool,
) -> list[NextStep]:
    """次のステップ提案リストを生成する（優先度順）。"""
    steps: list[NextStep] = []

    if current_stage == "onboarding":
        # オンボーディング中: 使い方ガイドを最優先
        steps.append(NextStep(
            feature="ナレッジを入力する",
            reason="AIの精度を上げるため、まず会社のルール・ノウハウを入力しましょう",
            expected_benefit="AIが自社の業務内容を理解し、正確な回答と提案ができるようになります",
            action_url="/knowledge/input",
            priority=1,
        ))
        steps.append(NextStep(
            feature="AIに質問する",
            reason="ナレッジを入力したら、AIに質問して使い心地を確かめましょう",
            expected_benefit="社内ルール・ノウハウへのQ&Aがいつでも自動応答されます",
            action_url="/knowledge/qa",
            priority=2,
        ))
        steps.append(NextStep(
            feature="業務自動化を試す",
            reason="AIが業務を自動でこなす体験をしてみましょう",
            expected_benefit="見積・請求・発注などの定型業務を自動化できます",
            action_url="/bpo",
            priority=3,
        ))
        return steps

    # 業種ペア提案: 使っている業種から次の業種を提案
    priority_counter = 1
    for used_pipeline, recommend_pipeline in _INDUSTRY_PAIRS:
        if used_pipeline in pipeline_types and recommend_pipeline not in pipeline_types:
            label = _PIPELINE_LABELS.get(recommend_pipeline, recommend_pipeline)
            url = _PIPELINE_URLS.get(recommend_pipeline, "/bpo")
            used_label = _PIPELINE_LABELS.get(used_pipeline, used_pipeline)
            steps.append(NextStep(
                feature=label,
                reason=f"{used_label}でのAI自動化が軌道に乗っています。同様の効果を{label}でも得られます",
                expected_benefit=f"{label}の定型業務を自動化し、月間作業時間を大幅に削減できます",
                action_url=url,
                priority=priority_counter,
            ))
            priority_counter += 1
            if len(steps) >= 3:
                break

    # GWS未連携の場合は連携を提案
    if not gws_connected and len(steps) < 3:
        steps.append(NextStep(
            feature="Googleカレンダー・Gmail連携",
            reason="Googleカレンダー・GmailをAIと連携すると、スケジュール管理やメール対応を自動化できます",
            expected_benefit="予定の自動登録・会議調整・メール仕分けが自動化されます",
            action_url="/settings",
            priority=priority_counter,
        ))
        priority_counter += 1

    # 利用率が高いユーザーへはAI提案機能を案内
    if current_stage in ("active_multi", "power_user") and len(steps) < 3:
        steps.append(NextStep(
            feature="AI提案を確認する",
            reason="十分なデータが蓄積されました。AIがリスクや改善機会を自動で検出します",
            expected_benefit="AIが会社のリスクや改善機会を定期的にレポートします",
            action_url="/proposals",
            priority=priority_counter,
        ))
        priority_counter += 1

    # 提案が足りない場合はデフォルト提案を追加
    if len(steps) < 3 and current_stage == "active_single" and total_runs >= _STAGE_ONBOARDING_MAX:
        steps.append(NextStep(
            feature="業務フローをさらに増やす",
            reason="現在の自動化フローが安定しています。他の業務にも展開しましょう",
            expected_benefit="複数の業務フローを自動化することで、相乗効果が生まれます",
            action_url="/bpo",
            priority=priority_counter,
        ))

    return steps

"""Benchmark Aggregator — 匿名ベンチマーク（ネットワーク効果）

REQ-2004: ネットワーク効果
同業種企業の集計データを元に、自社の利用状況を業界平均と比較する。

k-匿名化: 同業種企業が5社未満の場合はデータを返さない（プライバシー保護）。
他社の個別データは一切返さない（集計値のみ）。
"""
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from db.supabase import get_service_client
from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

# k-匿名化の閾値 — 同業種企業数がこれ未満はデータを返さない
_K_ANONYMITY_MIN_COMPANIES: int = 5

# 集計対象の期間（直近N ヶ月）
_RECENT_MONTHS: int = 3

# メトリクスタイプ定義
_METRIC_TYPES = [
    {
        "metric_type": "pipeline_run",
        "metric_name": "pipeline_run_monthly_avg",
        "unit": "回/月",
        "label": "業務自動化の実行回数",
    },
    {
        "metric_type": "qa_query",
        "metric_name": "qa_usage_monthly_avg",
        "unit": "回/月",
        "label": "AIへの質問回数",
    },
    {
        "metric_type": "connector_sync",
        "metric_name": "connector_sync_monthly_avg",
        "unit": "回/月",
        "label": "外部連携の同期回数",
    },
]


@dataclass
class BenchmarkMetric:
    """1メトリクスのベンチマーク結果。"""
    metric_name: str           # "pipeline_run_monthly_avg" 等
    my_value: float            # 自社の値
    industry_avg: float        # 業界平均
    industry_percentile: int   # 0-100（自社が上位何%か。100=最も高い）
    unit: str                  # "回/月", "%", "件" 等
    insight: str               # LLM生成の自然言語説明（1文・日本語）


@dataclass
class BenchmarkResult:
    """ベンチマーク集計結果。"""
    company_id: str
    industry: str
    company_count: Optional[int]         # 比較に使った企業数（k-匿名化: 5社未満はNone）
    metrics: list[BenchmarkMetric] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_available: bool = True
    unavailable_reason: Optional[str] = None


def _compute_percentile(values: list[float], my_value: float) -> int:
    """my_value が values の中で上位何%に位置するかを返す（0-100）。

    100 = 最も高い（全社を上回る）
    0 = 最も低い
    """
    if not values:
        return 50
    below_count = sum(1 for v in values if v < my_value)
    percentile = int(round(below_count / len(values) * 100))
    return percentile


def _aggregate_by_company(rows: list[dict]) -> dict[str, float]:
    """usage_metrics の行リストを company_id ごとの月平均に集計する。

    各社の月平均 = 期間内の合計 / 期間月数

    Returns:
        {company_id: monthly_avg}
    """
    company_totals: dict[str, float] = {}
    for row in rows:
        cid = row.get("company_id", "")
        qty = float(row.get("quantity", 0) or 0)
        company_totals[cid] = company_totals.get(cid, 0.0) + qty

    return {cid: total / _RECENT_MONTHS for cid, total in company_totals.items()}


async def _generate_insight(
    metric_label: str,
    my_value: float,
    industry_avg: float,
    percentile: int,
    unit: str,
    company_id: str,
) -> str:
    """LLMで自然言語のinsightを1文生成する。失敗時はルールベースにフォールバック。"""
    diff_pct = (
        round((my_value - industry_avg) / industry_avg * 100)
        if industry_avg > 0
        else 0
    )
    direction = "多く" if diff_pct >= 0 else "少なく"
    abs_diff = abs(diff_pct)

    prompt = (
        f"以下のデータをもとに、経営者向けに1文（50字以内）で端的なinsightを日本語で書いてください。\n"
        f"- 指標: {metric_label}\n"
        f"- 自社値: {my_value:.1f}{unit}\n"
        f"- 業界平均: {industry_avg:.1f}{unit}\n"
        f"- 業界内パーセンタイル: 上位{100 - percentile}%\n"
        f"insightのみを返してください（前置き・理由は不要）。"
    )

    try:
        llm = get_llm_client()
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": "あなたは中小企業経営者向けのビジネスアドバイザーです。"},
                {"role": "user", "content": prompt},
            ],
            tier=ModelTier.FAST,
            task_type="benchmark_insight",
            company_id=company_id,
            max_tokens=100,
        ))
        return response.content.strip()
    except Exception as e:
        logger.warning(f"benchmark_aggregator: LLMインサイト生成失敗 (フォールバック): {e}")
        # ルールベースフォールバック
        if abs_diff < 5:
            return f"同業他社平均とほぼ同水準で{metric_label}を活用しています。"
        return f"同業他社平均より{abs_diff}%{direction}{metric_label}を活用しています（上位{100 - percentile}%）。"


class BenchmarkAggregator:
    """同業種の匿名ベンチマークを計算するクラス。

    k-匿名化ルール: 同業種5社未満の場合は is_available=False を返す。
    他社個別データは返さない（集計値のみ）。
    """

    async def compute(self, company_id: str) -> BenchmarkResult:
        """company_id の企業のベンチマークを計算して返す。

        Args:
            company_id: 対象企業ID

        Returns:
            BenchmarkResult
        """
        db = get_service_client()

        # 1. 自社の industry を取得
        try:
            company_result = db.table("companies") \
                .select("industry") \
                .eq("id", company_id) \
                .single() \
                .execute()
            industry: Optional[str] = (
                company_result.data.get("industry") if company_result.data else None
            )
        except Exception as e:
            logger.warning(f"benchmark_aggregator: companies取得失敗 company_id={company_id}: {e}")
            return BenchmarkResult(
                company_id=company_id,
                industry="unknown",
                company_count=None,
                is_available=False,
                unavailable_reason="会社情報の取得に失敗しました。",
            )

        if not industry:
            return BenchmarkResult(
                company_id=company_id,
                industry="unknown",
                company_count=None,
                is_available=False,
                unavailable_reason="業種が未設定のため比較できません。設定画面から業種を登録してください。",
            )

        # 2. 同業種の company_id リストを取得
        try:
            peers_result = db.table("companies") \
                .select("id") \
                .eq("industry", industry) \
                .execute()
            peer_company_ids: list[str] = [
                row["id"] for row in (peers_result.data or [])
            ]
        except Exception as e:
            logger.warning(f"benchmark_aggregator: 同業種企業取得失敗: {e}")
            return BenchmarkResult(
                company_id=company_id,
                industry=industry,
                company_count=None,
                is_available=False,
                unavailable_reason="同業種データの取得に失敗しました。",
            )

        company_count = len(peer_company_ids)

        # 3. k-匿名化チェック（5社未満はデータ返さない）
        if company_count < _K_ANONYMITY_MIN_COMPANIES:
            return BenchmarkResult(
                company_id=company_id,
                industry=industry,
                company_count=None,
                is_available=False,
                unavailable_reason=(
                    f"同業種の登録企業数が少ないため、まだ比較データがありません。"
                    f"（現在{company_count}社。{_K_ANONYMITY_MIN_COMPANIES}社以上で有効化されます）"
                ),
            )

        # 4. 直近 _RECENT_MONTHS ヶ月の usage_metrics を集計
        now = datetime.now(timezone.utc)
        recent_periods = [
            (now.replace(month=now.month - i) if now.month > i
             else now.replace(year=now.year - 1, month=now.month - i + 12)
             ).strftime("%Y-%m")
            for i in range(_RECENT_MONTHS)
        ]

        metrics: list[BenchmarkMetric] = []

        for metric_def in _METRIC_TYPES:
            metric_type = metric_def["metric_type"]
            metric_name = metric_def["metric_name"]
            unit = metric_def["unit"]
            label = metric_def["label"]

            try:
                rows_result = db.table("usage_metrics") \
                    .select("company_id, quantity") \
                    .in_("company_id", peer_company_ids) \
                    .eq("metric_type", metric_type) \
                    .in_("period_month", recent_periods) \
                    .execute()
                rows: list[dict] = rows_result.data or []
            except Exception as e:
                logger.warning(
                    f"benchmark_aggregator: usage_metrics取得失敗 "
                    f"metric_type={metric_type}: {e}"
                )
                continue

            # company_id ごとの月平均を計算
            company_avgs = _aggregate_by_company(rows)

            # 自社の月平均
            my_avg = company_avgs.get(company_id, 0.0)

            # 業界平均（全社の平均）
            all_avgs = list(company_avgs.values())

            # usage_metricsが全くない企業は0として扱う
            # peer_company_ids に含まれるが rows に出てこない企業は月平均0
            for pid in peer_company_ids:
                if pid not in company_avgs:
                    all_avgs.append(0.0)

            if not all_avgs:
                continue

            industry_avg = statistics.mean(all_avgs)
            percentile = _compute_percentile(all_avgs, my_avg)

            # LLMでinsight生成
            insight = await _generate_insight(
                metric_label=label,
                my_value=my_avg,
                industry_avg=industry_avg,
                percentile=percentile,
                unit=unit,
                company_id=company_id,
            )

            metrics.append(BenchmarkMetric(
                metric_name=metric_name,
                my_value=round(my_avg, 1),
                industry_avg=round(industry_avg, 1),
                industry_percentile=percentile,
                unit=unit,
                insight=insight,
            ))

        return BenchmarkResult(
            company_id=company_id,
            industry=industry,
            company_count=company_count,
            metrics=metrics,
            is_available=True,
        )

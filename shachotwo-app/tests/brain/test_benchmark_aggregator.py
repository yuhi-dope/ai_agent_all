"""Tests for brain/analytics/benchmark_aggregator.py (REQ-2004 ネットワーク効果).

k-匿名化ロジック・集計計算・パーセンタイル計算をテストする。
外部API（LLM, Supabase）はすべてモック。
"""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.analytics.benchmark_aggregator import (
    BenchmarkAggregator,
    BenchmarkMetric,
    BenchmarkResult,
    _K_ANONYMITY_MIN_COMPANIES,
    _aggregate_by_company,
    _compute_percentile,
)

COMPANY_ID = str(uuid4())
INDUSTRY = "construction"

# ---------------------------------------------------------------------------
# ユーティリティ関数テスト
# ---------------------------------------------------------------------------


class TestComputePercentile:
    def test_empty_values_returns_50(self):
        assert _compute_percentile([], 10.0) == 50

    def test_highest_value_returns_100(self):
        values = [1.0, 2.0, 3.0, 4.0]
        assert _compute_percentile(values, 5.0) == 100

    def test_lowest_value_returns_0(self):
        values = [2.0, 3.0, 4.0, 5.0]
        assert _compute_percentile(values, 0.0) == 0

    def test_median_value_returns_around_50(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        pct = _compute_percentile(values, 3.0)
        assert 40 <= pct <= 60

    def test_single_value(self):
        values = [10.0]
        assert _compute_percentile(values, 10.0) == 0  # 自分以外は下回っていない

    def test_returns_int(self):
        result = _compute_percentile([1.0, 2.0, 3.0], 2.0)
        assert isinstance(result, int)


class TestAggregateByCompany:
    def test_empty_returns_empty(self):
        result = _aggregate_by_company([])
        assert result == {}

    def test_single_company_single_row(self):
        cid = str(uuid4())
        rows = [{"company_id": cid, "quantity": 30}]
        result = _aggregate_by_company(rows)
        # _RECENT_MONTHS=3 で割る
        assert cid in result
        assert result[cid] == pytest.approx(10.0, rel=1e-3)

    def test_multiple_rows_same_company(self):
        cid = str(uuid4())
        rows = [
            {"company_id": cid, "quantity": 30},
            {"company_id": cid, "quantity": 60},
        ]
        result = _aggregate_by_company(rows)
        # 合計90 / 3ヶ月 = 30
        assert result[cid] == pytest.approx(30.0, rel=1e-3)

    def test_multiple_companies(self):
        cid1, cid2 = str(uuid4()), str(uuid4())
        rows = [
            {"company_id": cid1, "quantity": 30},
            {"company_id": cid2, "quantity": 60},
        ]
        result = _aggregate_by_company(rows)
        assert cid1 in result
        assert cid2 in result
        assert result[cid1] == pytest.approx(10.0, rel=1e-3)
        assert result[cid2] == pytest.approx(20.0, rel=1e-3)

    def test_none_quantity_treated_as_zero(self):
        cid = str(uuid4())
        rows = [{"company_id": cid, "quantity": None}]
        result = _aggregate_by_company(rows)
        assert result[cid] == pytest.approx(0.0, rel=1e-3)


# ---------------------------------------------------------------------------
# BenchmarkAggregator.compute() — DBモック
# ---------------------------------------------------------------------------


def _make_db_mock(
    industry: str | None = INDUSTRY,
    peer_ids: list[str] | None = None,
    usage_rows: list[dict] | None = None,
):
    """DB モックを構築するヘルパー。"""
    if peer_ids is None:
        peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES)]
    if usage_rows is None:
        usage_rows = []

    db = MagicMock()

    # companies.single() — 自社 industry 取得
    company_data = {"industry": industry} if industry else None
    company_result = MagicMock()
    company_result.data = company_data
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .single.return_value
        .execute.return_value
    ) = company_result

    # companies（同業種一覧）— .eq().execute()
    peers_result = MagicMock()
    peers_result.data = [{"id": pid} for pid in peer_ids]
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .execute.return_value
    ) = peers_result

    # usage_metrics
    metrics_result = MagicMock()
    metrics_result.data = usage_rows
    (
        db.table.return_value
        .select.return_value
        .in_.return_value
        .eq.return_value
        .in_.return_value
        .execute.return_value
    ) = metrics_result

    return db


@pytest.mark.asyncio
async def test_compute_industry_not_set_returns_unavailable():
    """業種未設定の場合は is_available=False を返す。"""
    db = _make_db_mock(industry=None)
    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client"),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert isinstance(result, BenchmarkResult)
    assert result.is_available is False
    assert result.company_count is None
    assert result.unavailable_reason is not None


@pytest.mark.asyncio
async def test_compute_k_anonymity_too_few_companies():
    """同業種企業数が _K_ANONYMITY_MIN_COMPANIES 未満なら is_available=False。"""
    # 4社（閾値5社未満）
    peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES - 1)]
    db = _make_db_mock(peer_ids=peer_ids)
    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client"),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert result.is_available is False
    assert result.company_count is None
    assert result.unavailable_reason is not None
    assert str(_K_ANONYMITY_MIN_COMPANIES) in result.unavailable_reason


@pytest.mark.asyncio
async def test_compute_k_anonymity_exact_threshold():
    """ちょうど _K_ANONYMITY_MIN_COMPANIES 社ならデータを返す。"""
    peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES)]
    usage_rows = [
        {"company_id": pid, "quantity": 30}
        for pid in peer_ids
    ]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=usage_rows)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "同業他社平均と同水準で活用しています。"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert result.is_available is True
    assert result.company_count == _K_ANONYMITY_MIN_COMPANIES


@pytest.mark.asyncio
async def test_compute_returns_correct_industry():
    """返却された industry フィールドが正しい。"""
    peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES)]
    db = _make_db_mock(industry="manufacturing", peer_ids=peer_ids)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "インサイトのテキスト"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert result.industry == "manufacturing"
    assert result.company_id == COMPANY_ID


@pytest.mark.asyncio
async def test_compute_metrics_values_are_correct():
    """集計値（自社値・業界平均）の計算が正しい。"""
    # 5社: 自社=60回、他社4社=30回ずつ（3ヶ月分として記録）
    my_id = COMPANY_ID
    other_ids = [str(uuid4()) for _ in range(4)]
    peer_ids = [my_id] + other_ids

    usage_rows = [{"company_id": my_id, "quantity": 60}] + [
        {"company_id": oid, "quantity": 30} for oid in other_ids
    ]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=usage_rows)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "同業他社平均より多く活用しています。"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(my_id)

    assert result.is_available is True
    assert len(result.metrics) > 0

    m = result.metrics[0]
    assert isinstance(m, BenchmarkMetric)
    assert m.my_value >= 0
    assert m.industry_avg >= 0
    assert 0 <= m.industry_percentile <= 100
    assert m.unit
    assert m.insight


@pytest.mark.asyncio
async def test_compute_percentile_above_average():
    """自社が業界平均を上回る場合、パーセンタイルが50以上になる。"""
    my_id = COMPANY_ID
    other_ids = [str(uuid4()) for _ in range(4)]
    peer_ids = [my_id] + other_ids

    # 自社: 90回, 他社: 10回ずつ → 自社が圧倒的に多い
    usage_rows = [{"company_id": my_id, "quantity": 90}] + [
        {"company_id": oid, "quantity": 10} for oid in other_ids
    ]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=usage_rows)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "トップレベルの活用度です。"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(my_id)

    assert result.is_available is True
    if result.metrics:
        # 自社が最高値なのでパーセンタイルは高いはず
        assert result.metrics[0].industry_percentile >= 50


@pytest.mark.asyncio
async def test_compute_db_error_on_company_fetch():
    """companies テーブル取得エラー時は is_available=False を返す。"""
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.side_effect = Exception(
        "DB connection error"
    )

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client"),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert result.is_available is False
    assert result.company_count is None


@pytest.mark.asyncio
async def test_compute_llm_failure_falls_back_to_rule_based_insight():
    """LLM呼び出し失敗時もルールベースのinsightを返す（エラーにならない）。"""
    my_id = COMPANY_ID
    other_ids = [str(uuid4()) for _ in range(4)]
    peer_ids = [my_id] + other_ids
    usage_rows = [{"company_id": my_id, "quantity": 30}] + [
        {"company_id": oid, "quantity": 30} for oid in other_ids
    ]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=usage_rows)

    # LLM が例外を投げる
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(side_effect=Exception("LLM API error"))

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(my_id)

    assert result.is_available is True
    for m in result.metrics:
        # フォールバックのinsightが文字列として存在する
        assert isinstance(m.insight, str)
        assert len(m.insight) > 0


@pytest.mark.asyncio
async def test_compute_no_usage_data_all_zeros():
    """usage_metrics が全社ゼロでもエラーにならず結果を返す。"""
    peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES)]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=[])

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "まだデータがありません。"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert result.is_available is True
    for m in result.metrics:
        assert m.my_value == pytest.approx(0.0)
        assert m.industry_avg == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_compute_result_has_computed_at():
    """computed_at が datetime 型で返される。"""
    from datetime import datetime

    peer_ids = [str(uuid4()) for _ in range(_K_ANONYMITY_MIN_COMPANIES)]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=[])

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "インサイト"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    assert isinstance(result.computed_at, datetime)


@pytest.mark.asyncio
async def test_individual_company_data_not_exposed():
    """他社の個別 company_id は返却値に含まれない。"""
    other_ids = [str(uuid4()) for _ in range(4)]
    peer_ids = [COMPANY_ID] + other_ids
    usage_rows = [
        {"company_id": cid, "quantity": 30} for cid in peer_ids
    ]
    db = _make_db_mock(peer_ids=peer_ids, usage_rows=usage_rows)

    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "インサイト"
    mock_llm.generate = AsyncMock(return_value=mock_response)

    with (
        patch("brain.analytics.benchmark_aggregator.get_service_client", return_value=db),
        patch("brain.analytics.benchmark_aggregator.get_llm_client", return_value=mock_llm),
    ):
        aggregator = BenchmarkAggregator()
        result = await aggregator.compute(COMPANY_ID)

    # BenchmarkResult に他社の company_id が露出していないことを確認
    result_str = str(result)
    for oid in other_ids:
        assert oid not in result_str, f"他社ID {oid} がレスポンスに含まれています"

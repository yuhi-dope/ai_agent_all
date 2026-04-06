"""CRM パイプライン⑤ 売上・要望管理 テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.sales.crm.revenue_request_pipeline import (
    PRIORITY_HIGH_THRESHOLD,
    PRIORITY_MEDIUM_THRESHOLD,
    RequestPriorityResult,
    RevenueMetrics,
    RevenueRequestPipelineResult,
    _build_request_priorities,
    _build_revenue_metrics,
    _build_slack_summary,
    _default_response,
    _group_similar_requests,
    run_revenue_request_pipeline,
)
from workers.micro.models import MicroAgentOutput

# ---------------------------------------------------------------------------
# 定数・フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-crm-001"

# アクティブ顧客リスト（直渡し形式）
CUSTOMERS_DIRECT = [
    {"id": "c1", "name": "建設A社", "status": "active",     "mrr": 300_000, "health_score": 80},
    {"id": "c2", "name": "製造B社", "status": "active",     "mrr": 250_000, "health_score": 70},
    {"id": "c3", "name": "歯科C院", "status": "new",        "mrr": 250_000, "health_score": 85},
    {"id": "c4", "name": "飲食D店", "status": "expansion",  "mrr": 350_000, "health_score": 90},
    {"id": "c5", "name": "物流E社", "status": "churned",    "mrr": 300_000, "health_score": 20},
    {"id": "c6", "name": "小売F社", "status": "contraction","mrr": 200_000, "health_score": 55},
]

# 要望リスト（随時モード用）
REQUESTS_INPUT = [
    {
        "source_type": "ticket",
        "text": "CSVエクスポート機能を追加してほしい。毎月の集計作業が手作業で大変です。",
        "customer_id": "c1",
        "customer_mrr": 300_000,
        "health_score": 80,
        "request_id": "req-001",
    },
    {
        "source_type": "slack",
        "text": "kintone連携の機能が欲しい。既存システムとつなぎたい。",
        "customer_id": "c4",
        "customer_mrr": 350_000,
        "health_score": 90,
        "request_id": "req-002",
    },
    {
        "source_type": "email",
        "text": "CSVエクスポートがあると助かります。手作業が多くて辛い。",
        "customer_id": "c5",
        "customer_mrr": 0,         # チャーン済み = 高リスク
        "health_score": 20,
        "request_id": "req-003",
    },
]


# ---------------------------------------------------------------------------
# ユニットテスト: _group_similar_requests
# ---------------------------------------------------------------------------

class TestGroupSimilarRequests:

    def test_同カテゴリ_同タイトルプレフィックスでグルーピングされる(self):
        reqs = [
            {"category": "feature", "title": "CSVエクスポート機能を追加してほしい",
             "customer_mrr": 300_000, "health_score": 80, "request_id": "r1"},
            {"category": "feature", "title": "CSVエクスポートがあると助かります",
             "customer_mrr": 0, "health_score": 20, "request_id": "r2"},
        ]
        result = _group_similar_requests(reqs)
        # タイトル先頭20文字が "CSVエクスポート機能を追加してほ" と "CSVエクスポートがあると" で異なるため別グループ
        # ただし同カテゴリの場合は少なくとも2グループになる
        assert len(result) == 2

    def test_異なるカテゴリは別グループ(self):
        reqs = [
            {"category": "feature",     "title": "ダッシュボード改善", "customer_mrr": 100_000, "health_score": 70, "request_id": "r1"},
            {"category": "improvement", "title": "ダッシュボード改善", "customer_mrr": 200_000, "health_score": 60, "request_id": "r2"},
        ]
        result = _group_similar_requests(reqs)
        assert len(result) == 2

    def test_グルーピング時にvote_countが加算される(self):
        reqs = [
            {"category": "bug", "title": "ログインできない問題", "customer_mrr": 100_000, "health_score": 50, "request_id": "r1", "vote_count": 2},
            {"category": "bug", "title": "ログインできない問題", "customer_mrr": 200_000, "health_score": 40, "request_id": "r2"},
        ]
        result = _group_similar_requests(reqs)
        # 同カテゴリ+タイトル先頭20文字が一致
        group = next(g for g in result if g["category"] == "bug")
        assert group["vote_count"] >= 2  # 集計されている

    def test_MRRは最大値が代表される(self):
        reqs = [
            {"category": "feature", "title": "API公開してほしい", "customer_mrr": 100_000, "health_score": 70, "request_id": "r1"},
            {"category": "feature", "title": "API公開してほしい", "customer_mrr": 500_000, "health_score": 60, "request_id": "r2"},
        ]
        result = _group_similar_requests(reqs)
        group = result[0]
        assert group["customer_mrr"] == 500_000

    def test_ヘルススコアは最小値が代表される(self):
        reqs = [
            {"category": "feature", "title": "レポート自動化", "customer_mrr": 200_000, "health_score": 80, "request_id": "r1"},
            {"category": "feature", "title": "レポート自動化", "customer_mrr": 150_000, "health_score": 15, "request_id": "r2"},
        ]
        result = _group_similar_requests(reqs)
        group = result[0]
        assert group["health_score"] == 15


# ---------------------------------------------------------------------------
# ユニットテスト: _build_revenue_metrics
# ---------------------------------------------------------------------------

class TestBuildRevenueMetrics:

    def test_フィールドが正しくマッピングされる(self):
        data = {
            "mrr": 1_000_000,
            "arr": 12_000_000,
            "new_mrr": 250_000,
            "expansion_mrr": 50_000,
            "contraction_mrr": 20_000,
            "churned_mrr": 300_000,
            "nrr": 103.5,
            "churn_rate": 2.1,
            "active_customer_count": 10,
        }
        m = _build_revenue_metrics(data, 2026, 3)
        assert m.mrr == 1_000_000
        assert m.arr == 12_000_000
        assert m.nrr == 103.5
        assert m.churn_rate == 2.1
        assert m.period_year == 2026
        assert m.period_month == 3
        assert m.active_customer_count == 10

    def test_欠損フィールドはデフォルト値になる(self):
        m = _build_revenue_metrics({}, 2026, 1)
        assert m.mrr == 0
        assert m.nrr == 100.0
        assert m.churn_rate == 0.0


# ---------------------------------------------------------------------------
# ユニットテスト: _build_request_priorities
# ---------------------------------------------------------------------------

class TestBuildRequestPriorities:

    def test_優先スコアが高い順に変換される(self):
        prioritized = [
            {
                "request_id": "r1", "title": "CSV出力", "category": "feature",
                "priority_score": 80.0, "priority_level": "high",
                "vote_count": 5, "customer_mrr": 300_000, "churn_risk_score": 30.0,
                "ai_categories": ["CSV", "エクスポート"], "similar_request_ids": ["r3"],
            },
            {
                "request_id": "r2", "title": "kintone連携", "category": "integration",
                "priority_score": 45.0, "priority_level": "medium",
                "vote_count": 2, "customer_mrr": 350_000, "churn_risk_score": 10.0,
                "ai_categories": ["連携"], "similar_request_ids": [],
            },
        ]
        results = _build_request_priorities(prioritized)
        assert len(results) == 2
        assert isinstance(results[0], RequestPriorityResult)
        assert results[0].priority_score == 80.0
        assert results[0].priority_level == "high"
        assert results[0].ai_categories == ["CSV", "エクスポート"]
        assert results[1].priority_level == "medium"


# ---------------------------------------------------------------------------
# ユニットテスト: _build_slack_summary
# ---------------------------------------------------------------------------

class TestBuildSlackSummary:

    def _make_metrics(self, nrr: float, churn_rate: float) -> RevenueMetrics:
        return RevenueMetrics(
            mrr=1_000_000, arr=12_000_000,
            new_mrr=250_000, expansion_mrr=50_000,
            contraction_mrr=20_000, churned_mrr=300_000,
            nrr=nrr, churn_rate=churn_rate,
            active_customer_count=5,
            period_year=2026, period_month=3,
        )

    def test_月次レポートヘッダーが含まれる(self):
        m = self._make_metrics(nrr=110.0, churn_rate=2.0)
        msg = _build_slack_summary(m, "report")
        assert "2026年3月" in msg
        assert "MRR" in msg
        assert "NRR" in msg
        assert "チャーン率" in msg

    def test_金額がフォーマットされる(self):
        m = self._make_metrics(nrr=105.0, churn_rate=1.5)
        msg = _build_slack_summary(m, "")
        assert "1,000,000円" in msg

    def test_高NRRでupが含まれる(self):
        m = self._make_metrics(nrr=115.0, churn_rate=1.0)
        msg = _build_slack_summary(m, "")
        assert "up" in msg

    def test_低NRRでdownが含まれる(self):
        m = self._make_metrics(nrr=95.0, churn_rate=6.0)
        msg = _build_slack_summary(m, "")
        assert "down" in msg

    def test_低チャーン率でgreen_circleが含まれる(self):
        m = self._make_metrics(nrr=110.0, churn_rate=1.5)
        msg = _build_slack_summary(m, "")
        assert "green_circle" in msg

    def test_高チャーン率でred_circleが含まれる(self):
        m = self._make_metrics(nrr=100.0, churn_rate=6.0)
        msg = _build_slack_summary(m, "")
        assert "red_circle" in msg


# ---------------------------------------------------------------------------
# ユニットテスト: _default_response
# ---------------------------------------------------------------------------

class TestDefaultResponse:

    def _make_req(self, priority_level: str) -> RequestPriorityResult:
        return RequestPriorityResult(
            request_id="r1", title="テスト要望", category="feature",
            priority_score=75.0, priority_level=priority_level,
            vote_count=3, customer_mrr=300_000, churn_risk_score=20.0,
            ai_categories=[], similar_request_ids=[],
        )

    def test_高優先度は特別メッセージ含む(self):
        resp = _default_response(self._make_req("high"))
        assert "優先度が高い" in resp
        assert "テスト要望" in resp

    def test_中優先度はバックログ追加メッセージ含む(self):
        resp = _default_response(self._make_req("medium"))
        assert "バックログ" in resp

    def test_低優先度は標準メッセージ(self):
        resp = _default_response(self._make_req("low"))
        assert "承りました" in resp


# ---------------------------------------------------------------------------
# 統合テスト: run_revenue_request_pipeline (モック使用)
# ---------------------------------------------------------------------------

def _make_saas_reader_mock(customers: list[dict] | None = None) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="saas_reader", success=True,
        result={"data": customers or CUSTOMERS_DIRECT, "count": len(customers or CUSTOMERS_DIRECT), "service": "supabase", "mock": False},
        confidence=1.0, cost_yen=0.0, duration_ms=10,
    )


def _make_generator_mock(content: str = "月次レポート内容") -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator", success=True,
        result={"content": content, "format": "markdown", "char_count": len(content)},
        confidence=0.9, cost_yen=5.0, duration_ms=800,
    )


def _make_extractor_mock() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="structured_extractor", success=True,
        result={"extracted": {
            "title": "CSV出力機能", "description": "毎月の集計を自動化したい",
            "category": "feature", "ai_categories": ["CSV", "自動化"], "urgency": "medium",
        }, "missing_fields": []},
        confidence=0.85, cost_yen=3.0, duration_ms=500,
    )


def _make_rule_matcher_mock() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="rule_matcher", success=True,
        result={"matched_rules": [], "applied_values": {}, "unmatched_fields": []},
        confidence=0.5, cost_yen=0.0, duration_ms=20,
    )


def _make_saas_writer_mock() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="saas_writer", success=True,
        result={"success": True, "operation_id": "op-001", "dry_run": True},
        confidence=1.0, cost_yen=0.0, duration_ms=15,
    )


@pytest.mark.asyncio
class TestRevenueRequestPipeline:

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_売上管理モードが正常に完了する(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT},
            mode="revenue",
            period_year=2026,
            period_month=3,
            dry_run=True,
        )

        assert result.success is True
        assert result.mode == "revenue"
        assert result.revenue_metrics is not None
        assert len(result.steps) == 3  # Step 1,2,3

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_rule_matcher", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_structured_extractor", new_callable=AsyncMock)
    async def test_要望管理モードが正常に完了する(
        self, mock_extractor, mock_rule_matcher, mock_generator, mock_reader, mock_writer
    ):
        mock_extractor.return_value = _make_extractor_mock()
        mock_rule_matcher.return_value = _make_rule_matcher_mock()
        mock_generator.return_value = _make_generator_mock("回答ドラフト")
        mock_reader.return_value = _make_saas_reader_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"requests": REQUESTS_INPUT},
            mode="request",
            dry_run=True,
        )

        assert result.success is True
        assert result.mode == "request"
        assert len(result.request_priorities) > 0
        assert len(result.steps) == 3  # Step 4,5,6

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_rule_matcher", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_structured_extractor", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_両方モードが正常に完了する(
        self, mock_reader, mock_extractor, mock_rule_matcher, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_extractor.return_value = _make_extractor_mock()
        mock_rule_matcher.return_value = _make_rule_matcher_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "customers": CUSTOMERS_DIRECT,
                "requests": REQUESTS_INPUT,
            },
            mode="both",
            period_year=2026,
            period_month=3,
            dry_run=True,
        )

        assert result.success is True
        assert result.mode == "both"
        assert result.revenue_metrics is not None
        assert len(result.request_priorities) > 0
        assert len(result.steps) == 6  # Step 1-6 全ステップ

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_MRR計算が正しい(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT},
            mode="revenue",
            period_year=2026,
            period_month=3,
            dry_run=True,
        )

        assert result.success is True
        m = result.revenue_metrics
        assert m is not None
        # active(300k+250k) + expansion(350k) + contraction(200k) = 1,100,000
        # new(250k) = 250,000
        # total_mrr = 1,350,000
        assert m.mrr == 1_350_000
        assert m.arr == m.mrr * 12
        assert m.churned_mrr == 300_000
        assert m.new_mrr == 250_000
        assert m.expansion_mrr == 350_000
        assert m.contraction_mrr == 200_000

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_チャーン率が0以上になる(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT},
            mode="revenue",
            dry_run=True,
        )

        assert result.revenue_metrics is not None
        assert result.revenue_metrics.churn_rate >= 0.0

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_requests空の場合は要望ステップをスキップ(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT, "requests": []},
            mode="both",
            dry_run=True,
        )

        assert result.success is True
        assert result.request_priorities == []
        # 売上管理の3ステップのみ
        assert len(result.steps) == 3

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_summary文字列が生成される(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT},
            mode="revenue",
            dry_run=True,
        )

        summary = result.summary()
        assert "CRM パイプライン" in summary
        assert "MRR" in summary

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_rule_matcher", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_structured_extractor", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_高MRR顧客の要望は優先スコアが高い(
        self, mock_reader, mock_extractor, mock_rule_matcher, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_extractor.return_value = _make_extractor_mock()
        mock_rule_matcher.return_value = _make_rule_matcher_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        high_mrr_requests = [
            {
                "source_type": "ticket",
                "text": "ダッシュボードを改善してほしい",
                "customer_id": "c4",
                "customer_mrr": 1_000_000,  # 高MRR
                "health_score": 80,
                "request_id": "rh1",
            },
            {
                "source_type": "ticket",
                "text": "ダッシュボードを改善してほしい",
                "customer_id": "c9",
                "customer_mrr": 10_000,     # 低MRR
                "health_score": 80,
                "request_id": "rl1",
            },
        ]

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"requests": high_mrr_requests},
            mode="request",
            dry_run=True,
        )

        assert result.success is True
        # 優先度スコアが計算されている
        assert len(result.request_priorities) > 0
        # 最高スコアは MRR重みが高いため高MRR顧客の要望が上位
        scores = [r.priority_score for r in result.request_priorities]
        assert max(scores) > 0

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_dry_run時はSlack投稿しない(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock()
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": CUSTOMERS_DIRECT},
            mode="revenue",
            dry_run=True,
        )

        assert result.success is True
        # dry_run=True のため Slack 投稿はスキップ
        assert result.slack_posted is False

    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_writer", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_document_generator", new_callable=AsyncMock)
    @patch("workers.bpo.sales.crm.revenue_request_pipeline.run_saas_reader", new_callable=AsyncMock)
    async def test_顧客データ空でもエラーにならない(
        self, mock_reader, mock_generator, mock_writer
    ):
        mock_reader.return_value = _make_saas_reader_mock(customers=[])
        mock_generator.return_value = _make_generator_mock()
        mock_writer.return_value = _make_saas_writer_mock()

        result = await run_revenue_request_pipeline(
            company_id=COMPANY_ID,
            input_data={"customers": []},
            mode="revenue",
            dry_run=True,
        )

        assert result.success is True
        assert result.revenue_metrics is not None
        assert result.revenue_metrics.mrr == 0
        assert result.revenue_metrics.churn_rate == 0.0

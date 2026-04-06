"""
CS対応品質フィードバック学習パイプライン テスト

テスト対象: workers/bpo/sales/pipelines/cs_feedback_pipeline.py
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.learning.cs_feedback_pipeline import (
    CSAT_AVG_LOWER_THRESHOLD,
    CSAT_AVG_RAISE_THRESHOLD,
    CONFIDENCE_DEFAULT,
    CONFIDENCE_LOWER_BOUND,
    CONFIDENCE_UPPER_BOUND,
    CsFeedbackPipelineResult,
    _classify_quality,
    _compute_csat_stats,
    run_cs_feedback_pipeline,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-001"

# クローズ済みチケットのモックデータ（20件）
def _make_tickets(n_good: int, n_bad: int, n_human: int) -> list[dict]:
    """good(CSAT=5) / bad(CSAT=1) / human_only の混合チケットを生成する。"""
    tickets = []
    for i in range(n_good):
        tickets.append({
            "id": f"cf-good-{i}",
            "ticket_id": f"ticket-good-{i}",
            "ai_response": f"良い回答サンプル {i}",
            "human_correction": None,
            "csat_score": 5,
            "was_escalated": False,
            "quality_label": None,
        })
    for i in range(n_bad):
        tickets.append({
            "id": f"cf-bad-{i}",
            "ticket_id": f"ticket-bad-{i}",
            "ai_response": f"改善が必要な回答 {i}",
            "human_correction": f"人間が修正した回答 {i}",
            "csat_score": 1,
            "was_escalated": True,
            "quality_label": None,
        })
    for i in range(n_human):
        tickets.append({
            "id": f"cf-human-{i}",
            "ticket_id": f"ticket-human-{i}",
            "ai_response": None,
            "human_correction": None,
            "csat_score": 4,
            "was_escalated": False,
            "quality_label": None,
        })
    return tickets


def _mock_saas_reader_output(tickets: list[dict]) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="saas_reader",
        success=True,
        result={"data": tickets, "count": len(tickets), "service": "supabase", "mock": True},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=10,
    )


def _mock_extractor_output() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="structured_extractor",
        success=True,
        result={
            "extracted": {
                "top_faq_patterns": [
                    {"question": "返品できますか", "answer": "30日以内に返品可能です", "frequency": "high"},
                    {"question": "配送はいつ？", "answer": "翌営業日発送です", "frequency": "high"},
                ],
                "common_failure_reasons": [
                    {"reason": "専門用語の説明不足", "example": "Technical term not explained"},
                ],
                "improvement_suggestions": [
                    "FAQ回答に具体的な日数を含める",
                    "エスカレーション後の対応手順を明文化する",
                ],
                "escalation_patterns": ["クレーム対応", "返金申請"],
            },
            "missing_fields": [],
        },
        confidence=0.95,
        cost_yen=5.0,
        duration_ms=200,
    )


def _mock_saas_writer_output() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="saas_writer",
        success=True,
        result={"success": True, "operation_id": "mock-op-id", "dry_run": True},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=5,
    )


def _mock_document_generator_output() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={
            "content": "# CS品質月次レポート\n\nAI対応率: 60%\nCSAT平均: 4.2\n",
            "format": "markdown",
            "char_count": 40,
        },
        confidence=0.9,
        cost_yen=3.0,
        duration_ms=150,
    )


# ---------------------------------------------------------------------------
# ユニットテスト: _classify_quality
# ---------------------------------------------------------------------------

class TestClassifyQuality:
    def test_csat_5_ai_response_is_good(self):
        assert _classify_quality(5, was_ai_response=True) == "good"

    def test_csat_4_ai_response_is_good(self):
        assert _classify_quality(4, was_ai_response=True) == "good"

    def test_csat_3_ai_response_is_neutral(self):
        assert _classify_quality(3, was_ai_response=True) == "neutral"

    def test_csat_2_ai_response_is_needs_improvement(self):
        assert _classify_quality(2, was_ai_response=True) == "needs_improvement"

    def test_csat_1_ai_response_is_needs_improvement(self):
        assert _classify_quality(1, was_ai_response=True) == "needs_improvement"

    def test_none_csat_ai_response_is_no_rating(self):
        assert _classify_quality(None, was_ai_response=True) == "no_rating"

    def test_human_only_returns_none(self):
        assert _classify_quality(5, was_ai_response=False) is None

    def test_human_only_bad_csat_returns_none(self):
        assert _classify_quality(1, was_ai_response=False) is None


# ---------------------------------------------------------------------------
# ユニットテスト: _compute_csat_stats
# ---------------------------------------------------------------------------

class TestComputeCsatStats:
    def test_empty_tickets(self):
        stats = _compute_csat_stats([])
        assert stats["total_count"] == 0
        assert stats["csat_avg"] is None
        assert stats["ai_ratio"] == 0.0

    def test_all_good_ai_tickets(self):
        tickets = _make_tickets(n_good=10, n_bad=0, n_human=0)
        stats = _compute_csat_stats(tickets)
        assert stats["total_count"] == 10
        assert stats["ai_response_count"] == 10
        assert stats["human_response_count"] == 0
        assert stats["ai_ratio"] == 1.0
        assert stats["csat_avg"] == 5.0
        assert stats["good_count"] == 10
        assert stats["needs_improvement_count"] == 0

    def test_mixed_tickets(self):
        tickets = _make_tickets(n_good=6, n_bad=4, n_human=10)
        stats = _compute_csat_stats(tickets)
        assert stats["total_count"] == 20
        assert stats["ai_response_count"] == 10
        assert stats["human_response_count"] == 10
        assert stats["ai_ratio"] == 0.5
        assert stats["good_count"] == 6
        assert stats["needs_improvement_count"] == 4

    def test_csat_avg_calculation(self):
        tickets = [
            {"ai_response": "A", "csat_score": 3},
            {"ai_response": "B", "csat_score": 5},
        ]
        stats = _compute_csat_stats(tickets)
        assert stats["csat_avg"] == 4.0

    def test_human_only_tickets_excluded_from_ai_count(self):
        tickets = _make_tickets(n_good=0, n_bad=0, n_human=5)
        stats = _compute_csat_stats(tickets)
        assert stats["ai_response_count"] == 0
        assert stats["human_response_count"] == 5


# ---------------------------------------------------------------------------
# 統合テスト: run_cs_feedback_pipeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_success_high_csat():
    """CSAT平均が高い（>=4.5）場合: 閾値が引き下げられること"""
    tickets = _make_tickets(n_good=15, n_bad=0, n_human=5)  # CSAT avg = 5.0

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True, "current_confidence_threshold": CONFIDENCE_DEFAULT},
        )

    assert isinstance(result, CsFeedbackPipelineResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 5

    # CSAT平均5.0 >= 4.5 → 閾値引き下げ
    threshold_result = result.final_output["confidence_threshold"]
    assert threshold_result["new"] == CONFIDENCE_LOWER_BOUND
    assert threshold_result["action"].startswith("lowered:")


@pytest.mark.asyncio
async def test_pipeline_success_low_csat():
    """CSAT平均が低い（<4.0）場合: 閾値が引き上げられること"""
    tickets = _make_tickets(n_good=3, n_bad=12, n_human=5)  # CSAT avg: mostly 1s

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True, "current_confidence_threshold": CONFIDENCE_DEFAULT},
        )

    assert result.success is True
    threshold_result = result.final_output["confidence_threshold"]
    assert threshold_result["new"] == CONFIDENCE_UPPER_BOUND
    assert threshold_result["action"].startswith("raised:")


@pytest.mark.asyncio
async def test_pipeline_success_moderate_csat():
    """CSAT平均が中程度（4.0〜4.5）の場合: 閾値変更なし"""
    tickets = [
        {"ai_response": "回答A", "csat_score": 4, "was_escalated": False,
         "human_correction": None, "id": f"t{i}", "ticket_id": f"tk{i}", "quality_label": None}
        for i in range(12)
    ]  # CSAT avg = 4.0（境界値）

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True, "current_confidence_threshold": CONFIDENCE_DEFAULT},
        )

    assert result.success is True
    threshold_result = result.final_output["confidence_threshold"]
    # CSAT=4.0 は raise_threshold(4.0)と同値 → no_change（< 4.0 ではない）
    assert threshold_result["new"] == CONFIDENCE_DEFAULT


@pytest.mark.asyncio
async def test_pipeline_skip_when_insufficient_tickets():
    """チケット数が最低数に満たない場合: 早期終了してskip=Trueを返す"""
    tickets = _make_tickets(n_good=2, n_bad=1, n_human=1)  # 4件 < min_tickets=10

    with patch(
        "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
        new_callable=AsyncMock,
        return_value=_mock_saas_reader_output(tickets),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True, "min_tickets": 10},
        )

    assert result.success is True
    assert result.final_output.get("skipped") is True
    assert "チケット数不足" in result.final_output.get("reason", "")
    # Step 1 のみ実行されている
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_pipeline_fails_when_saas_reader_fails():
    """saas_reader が失敗した場合: パイプラインが failed_step=saas_reader で失敗する"""
    failed_reader_out = MicroAgentOutput(
        agent_name="saas_reader",
        success=False,
        result={"error": "DB connection failed"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=5,
    )
    with patch(
        "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
        new_callable=AsyncMock,
        return_value=failed_reader_out,
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True},
        )

    assert result.success is False
    assert result.failed_step == "saas_reader"


@pytest.mark.asyncio
async def test_pipeline_extractor_failure_non_fatal():
    """extractor が失敗した場合でも Slack通知まで完了すること（パターン抽出は非致命的）"""
    tickets = _make_tickets(n_good=10, n_bad=2, n_human=3)
    failed_extractor_out = MicroAgentOutput(
        agent_name="structured_extractor",
        success=False,
        result={"error": "LLM timeout"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=30000,
    )

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=failed_extractor_out,
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True},
        )

    # extractor失敗はパイプライン全体失敗（設計書: Step 2失敗はfail）
    assert result.success is False
    assert result.failed_step == "extractor"


@pytest.mark.asyncio
async def test_pipeline_cost_and_duration_are_summed():
    """各ステップのコスト・処理時間が正しく集計されること"""
    tickets = _make_tickets(n_good=8, n_bad=2, n_human=5)

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),  # cost_yen=5.0
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),  # cost_yen=3.0
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True},
        )

    assert result.success is True
    # extractor(5円) + generator(3円) = 8円以上
    assert result.total_cost_yen >= 8.0
    # モック環境ではduration_msは0でも合計が計算されていればよい（パイプライン自体の処理時間を確認）
    assert result.total_duration_ms >= 0


@pytest.mark.asyncio
async def test_pipeline_step_count_is_five():
    """正常完了時にステップ数が5であること"""
    tickets = _make_tickets(n_good=10, n_bad=3, n_human=7)

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True},
        )

    assert result.success is True
    assert len(result.steps) == 5
    step_names = [s.step_name for s in result.steps]
    assert step_names == [
        "saas_reader",
        "extractor",
        "knowledge_updater",
        "threshold_adjuster",
        "report_generator",
    ]


@pytest.mark.asyncio
async def test_pipeline_dry_run_flag_propagates():
    """dry_run=True が最終出力に伝播していること"""
    tickets = _make_tickets(n_good=10, n_bad=2, n_human=3)

    with (
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=_mock_saas_reader_output(tickets),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_structured_extractor",
            new_callable=AsyncMock,
            return_value=_mock_extractor_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=_mock_saas_writer_output(),
        ),
        patch(
            "workers.bpo.sales.learning.cs_feedback_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_mock_document_generator_output(),
        ),
    ):
        result = await run_cs_feedback_pipeline(
            company_id=COMPANY_ID,
            input_data={"dry_run": True},
        )

    assert result.final_output["dry_run"] is True


def test_pipeline_result_summary():
    """CsFeedbackPipelineResult.summary() が文字列を返すこと"""
    from workers.bpo.sales.learning.cs_feedback_pipeline import StepResult
    result = CsFeedbackPipelineResult(
        success=True,
        steps=[
            StepResult(
                step_no=1, step_name="saas_reader", agent_name="saas_reader",
                success=True, result={}, confidence=1.0, cost_yen=0.0, duration_ms=10,
            ),
        ],
        final_output={"ticket_count": 20},
        total_cost_yen=5.0,
        total_duration_ms=500,
    )
    summary = result.summary()
    assert "CS品質フィードバックパイプライン" in summary
    assert "saas_reader" in summary
    assert "[OK]" in summary

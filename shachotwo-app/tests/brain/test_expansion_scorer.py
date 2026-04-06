"""Tests for brain/proactive/expansion_scorer.py (REQ-1504 Land and Expand)."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.proactive.expansion_scorer import (
    ExpansionScorer,
    ExpansionResult,
    NextStep,
    _determine_stage,
    _compute_usage_score,
    _build_next_steps,
)

COMPANY_ID = str(uuid4())


# ---------------------------------------------------------------------------
# ステージ判定
# ---------------------------------------------------------------------------

class TestDetermineStage:
    def test_no_runs_is_onboarding(self):
        assert _determine_stage(0, set()) == "onboarding"

    def test_few_runs_is_onboarding(self):
        assert _determine_stage(5, {"construction"}) == "onboarding"

    def test_single_pipeline_active(self):
        assert _determine_stage(20, {"construction"}) == "active_single"

    def test_multi_pipeline_active(self):
        assert _determine_stage(30, {"construction", "manufacturing"}) == "active_multi"

    def test_many_runs_is_power_user(self):
        assert _determine_stage(300, {"construction", "manufacturing"}) == "power_user"


# ---------------------------------------------------------------------------
# 使い込みスコア
# ---------------------------------------------------------------------------

class TestComputeUsageScore:
    def test_onboarding_zero(self):
        score = _compute_usage_score(0, "onboarding")
        assert 0.0 <= score <= 0.3

    def test_onboarding_partial(self):
        score = _compute_usage_score(5, "onboarding")
        assert 0.0 < score <= 0.3

    def test_active_single_range(self):
        score = _compute_usage_score(30, "active_single")
        assert 0.3 <= score <= 0.7

    def test_power_user_near_one(self):
        score = _compute_usage_score(500, "power_user")
        assert score <= 1.0
        assert score >= 0.9


# ---------------------------------------------------------------------------
# 次のステップ提案
# ---------------------------------------------------------------------------

class TestBuildNextSteps:
    def test_onboarding_suggests_knowledge_input(self):
        steps = _build_next_steps(
            current_stage="onboarding",
            pipeline_counts={},
            pipeline_types=set(),
            total_runs=0,
            gws_connected=False,
        )
        assert len(steps) <= 3
        features = [s.feature for s in steps]
        assert any("ナレッジ" in f for f in features)

    def test_construction_suggests_manufacturing(self):
        steps = _build_next_steps(
            current_stage="active_single",
            pipeline_counts={"construction": 15},
            pipeline_types={"construction"},
            total_runs=15,
            gws_connected=True,
        )
        features = [s.feature for s in steps]
        assert any("製造" in f for f in features)

    def test_manufacturing_user_no_duplicate_suggest(self):
        steps = _build_next_steps(
            current_stage="active_multi",
            pipeline_counts={"construction": 20, "manufacturing": 20},
            pipeline_types={"construction", "manufacturing"},
            total_runs=40,
            gws_connected=True,
        )
        features = [s.feature for s in steps]
        # すでに使っている業種は提案されない
        assert not any(f == "建設業務自動化" for f in features)
        assert not any(f == "製造業務自動化" for f in features)

    def test_gws_not_connected_suggests_gws(self):
        steps = _build_next_steps(
            current_stage="active_single",
            pipeline_counts={"construction": 20},
            pipeline_types={"construction"},
            total_runs=20,
            gws_connected=False,
        )
        features = [s.feature for s in steps]
        assert any("Google" in f or "カレンダー" in f for f in features)

    def test_max_three_steps(self):
        steps = _build_next_steps(
            current_stage="active_multi",
            pipeline_counts={"construction": 30},
            pipeline_types={"construction"},
            total_runs=30,
            gws_connected=False,
        )
        assert len(steps) <= 3

    def test_steps_have_required_fields(self):
        steps = _build_next_steps(
            current_stage="onboarding",
            pipeline_counts={},
            pipeline_types=set(),
            total_runs=0,
            gws_connected=False,
        )
        for step in steps:
            assert step.feature
            assert step.reason
            assert step.expected_benefit
            assert step.action_url.startswith("/")
            assert isinstance(step.priority, int)


# ---------------------------------------------------------------------------
# ExpansionScorer.score() — DB モック
# ---------------------------------------------------------------------------

def _make_db_mock(metrics_data=None, gws_count=0):
    """usage_metrics と watch_channels のモックDBを返す。"""
    db = MagicMock()

    # usage_metrics mock
    metrics_result = MagicMock()
    metrics_result.data = metrics_data or []
    metrics_chain = (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
    )
    metrics_chain.execute.return_value = metrics_result

    # watch_channels mock
    gws_result = MagicMock()
    gws_result.count = gws_count
    gws_chain = (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
    )
    gws_chain.execute.return_value = gws_result

    return db


@pytest.mark.asyncio
async def test_score_empty_metrics_returns_onboarding():
    """usage_metricsが空の場合はonboardingステージを返す。"""
    db = MagicMock()

    # usage_metricsの呼び出しチェーンをモック
    metrics_result = MagicMock()
    metrics_result.data = []
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = metrics_result

    # watch_channelsの呼び出しチェーンをモック
    gws_result = MagicMock()
    gws_result.count = 0
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = gws_result

    with patch("brain.proactive.expansion_scorer.get_service_client", return_value=db):
        scorer = ExpansionScorer()
        result = await scorer.score(COMPANY_ID)

    assert isinstance(result, ExpansionResult)
    assert result.company_id == COMPANY_ID
    assert result.current_stage == "onboarding"
    assert 0.0 <= result.usage_score <= 1.0
    assert len(result.next_steps) <= 3


@pytest.mark.asyncio
async def test_score_with_construction_runs():
    """建設業パイプラインの実行データがある場合、製造業を提案する。"""
    db = MagicMock()

    metrics_result = MagicMock()
    metrics_result.data = [
        {"pipeline_name": "construction", "quantity": 20},
    ]
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = metrics_result

    gws_result = MagicMock()
    gws_result.count = 1
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = gws_result

    with patch("brain.proactive.expansion_scorer.get_service_client", return_value=db):
        scorer = ExpansionScorer()
        result = await scorer.score(COMPANY_ID)

    assert result.current_stage == "active_single"
    features = [s.feature for s in result.next_steps]
    assert any("製造" in f for f in features)


@pytest.mark.asyncio
async def test_score_db_error_returns_onboarding():
    """DBエラー時はonboardingステージとして扱う。"""
    db = MagicMock()
    db.table.return_value.select.return_value.eq.side_effect = Exception("DB connection error")

    with patch("brain.proactive.expansion_scorer.get_service_client", return_value=db):
        scorer = ExpansionScorer()
        result = await scorer.score(COMPANY_ID)

    assert result.current_stage == "onboarding"
    assert result.company_id == COMPANY_ID


@pytest.mark.asyncio
async def test_score_result_has_computed_at():
    """computed_at が datetime 型で返される。"""
    from datetime import datetime

    db = MagicMock()
    metrics_result = MagicMock()
    metrics_result.data = []
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = metrics_result

    gws_result = MagicMock()
    gws_result.count = 0
    (
        db.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .execute.return_value
    ) = gws_result

    with patch("brain.proactive.expansion_scorer.get_service_client", return_value=db):
        scorer = ExpansionScorer()
        result = await scorer.score(COMPANY_ID)

    assert isinstance(result.computed_at, datetime)

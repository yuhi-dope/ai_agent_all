"""Eval frameworkのユニットテスト（LLMをモック）"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.knowledge.eval import (
    CONSTRUCTION_GOLDEN,
    EvalReport,
    EvalResult,
    GoldenSample,
    _aggregate_group,
    _calculate_keyword_hit_rate,
    run_eval,
)


# ---------------------------------------------------------------
# ヘルパー: ダミーQAResultを返すモック
# ---------------------------------------------------------------

def _make_mock_qa_result(answer: str, confidence: float = 0.7, cost_yen: float = 0.01) -> MagicMock:
    m = MagicMock()
    m.answer = answer
    m.confidence = confidence
    m.cost_yen = cost_yen
    m.search_mode = "enhanced"
    return m


# ---------------------------------------------------------------
# keyword_hit_rate の計算ロジック
# ---------------------------------------------------------------

class TestCalculateKeywordHitRate:
    def test_all_keywords_found(self):
        rate = _calculate_keyword_hit_rate("単価は1,000円/m3です", ["円", "m3", "単価"])
        assert rate == 1.0

    def test_partial_hit(self):
        rate = _calculate_keyword_hit_rate("単価は1,000円です", ["円", "m3", "単価"])
        # "円"と"単価"が含まれ、"m3"は含まれない → 2/3
        assert abs(rate - 2 / 3) < 0.001

    def test_no_keywords_found(self):
        rate = _calculate_keyword_hit_rate("関連情報がありません", ["円", "m3", "単価"])
        assert rate == 0.0

    def test_empty_keywords(self):
        # キーワードが空の場合は常に1.0
        rate = _calculate_keyword_hit_rate("任意のテキスト", [])
        assert rate == 1.0

    def test_case_insensitive(self):
        # 大文字小文字を区別しない
        rate = _calculate_keyword_hit_rate("The Cost is 500 YEN", ["cost", "yen"])
        assert rate == 1.0

    def test_single_keyword_hit(self):
        rate = _calculate_keyword_hit_rate("工期は3週間です", ["週", "月"])
        # "週"は含まれ、"月"は含まれない → 1/2
        assert abs(rate - 0.5) < 0.001


# ---------------------------------------------------------------
# _aggregate_group の集計ロジック
# ---------------------------------------------------------------

class TestAggregateGroup:
    def _make_result(self, confidence: float, khr: float, cost: float, latency: float) -> EvalResult:
        return EvalResult(
            question="テスト",
            answer="テスト回答",
            confidence=confidence,
            keyword_hit_rate=khr,
            search_mode="enhanced",
            cost_yen=cost,
            latency_ms=latency,
        )

    def test_empty_list(self):
        result = _aggregate_group([])
        assert result["count"] == 0
        assert result["avg_confidence"] == 0.0
        assert result["avg_keyword_hit_rate"] == 0.0

    def test_single_item(self):
        r = self._make_result(0.8, 1.0, 0.05, 200.0)
        result = _aggregate_group([r])
        assert result["count"] == 1
        assert result["avg_confidence"] == 0.8
        assert result["avg_keyword_hit_rate"] == 1.0
        assert result["avg_cost_yen"] == 0.05
        assert result["avg_latency_ms"] == 200.0

    def test_multiple_items_averages(self):
        items = [
            self._make_result(0.6, 0.5, 0.02, 100.0),
            self._make_result(0.8, 1.0, 0.04, 200.0),
        ]
        result = _aggregate_group(items)
        assert result["count"] == 2
        assert abs(result["avg_confidence"] - 0.7) < 0.001
        assert abs(result["avg_keyword_hit_rate"] - 0.75) < 0.001
        assert abs(result["avg_cost_yen"] - 0.03) < 0.0001
        assert abs(result["avg_latency_ms"] - 150.0) < 0.1


# ---------------------------------------------------------------
# GoldenSample モデルのバリデーション
# ---------------------------------------------------------------

class TestGoldenSample:
    def test_valid_sample(self):
        s = GoldenSample(
            question="単価を教えてください",
            expected_keywords=["円", "単価"],
            category="construction",
            difficulty="easy",
        )
        assert s.difficulty == "easy"
        assert len(s.expected_keywords) == 2

    def test_invalid_difficulty(self):
        with pytest.raises(Exception):
            GoldenSample(
                question="Q",
                expected_keywords=[],
                category="construction",
                difficulty="very_hard",  # 無効な値
            )

    def test_construction_golden_dataset_is_valid(self):
        assert len(CONSTRUCTION_GOLDEN) >= 3
        for sample in CONSTRUCTION_GOLDEN:
            assert sample.category == "construction"
            assert sample.difficulty in ("easy", "medium", "hard")
            assert len(sample.expected_keywords) > 0


# ---------------------------------------------------------------
# EvalReport の構造テスト
# ---------------------------------------------------------------

class TestEvalReport:
    def test_fields_present(self):
        report = EvalReport(
            total=3,
            avg_confidence=0.75,
            avg_keyword_hit_rate=0.8,
            avg_cost_yen=0.02,
            avg_latency_ms=300.0,
            by_difficulty={"easy": {"count": 1}, "medium": {"count": 1}, "hard": {"count": 1}},
            by_category={"construction": {"count": 3}},
            timestamp="2026-03-20T00:00:00+00:00",
        )
        assert report.total == 3
        assert "easy" in report.by_difficulty
        assert "construction" in report.by_category


# ---------------------------------------------------------------
# run_eval 統合テスト（LLMをモック）
# ---------------------------------------------------------------

class TestRunEval:
    @pytest.mark.asyncio
    async def test_empty_dataset_returns_zero_report(self):
        report = await run_eval(company_id=str(uuid4()), golden_dataset=[])
        assert report.total == 0
        assert report.avg_confidence == 0.0
        assert report.avg_keyword_hit_rate == 0.0

    @pytest.mark.asyncio
    async def test_run_eval_with_mock(self):
        """LLMをモックしてrun_evalの集計ロジックをテスト。"""
        company_id = str(uuid4())
        dataset = [
            GoldenSample(
                question="コンクリート単価は？",
                expected_keywords=["円", "m3"],
                category="construction",
                difficulty="easy",
            ),
            GoldenSample(
                question="工期はどれくらいですか？",
                expected_keywords=["日", "週"],
                category="construction",
                difficulty="medium",
            ),
        ]

        mock_qa_results = [
            _make_mock_qa_result("1,000円/m3で計算します", confidence=0.85, cost_yen=0.01),
            _make_mock_qa_result("標準的な工期は2週間程度です", confidence=0.70, cost_yen=0.01),
        ]

        call_count = 0

        async def mock_answer_question(question, company_id, **kwargs):
            nonlocal call_count
            result = mock_qa_results[call_count]
            call_count += 1
            return result

        with patch("brain.knowledge.eval.answer_question", side_effect=mock_answer_question):
            report = await run_eval(company_id=company_id, golden_dataset=dataset)

        assert report.total == 2
        # 第1問: "円"はヒット, "m3"はヒット → 1.0
        # 第2問: "日"は不ヒット, "週"はヒット → 0.5
        # 平均: 0.75
        assert abs(report.avg_keyword_hit_rate - 0.75) < 0.01
        assert abs(report.avg_confidence - 0.775) < 0.01
        assert "easy" in report.by_difficulty
        assert "medium" in report.by_difficulty
        assert "construction" in report.by_category
        assert report.by_category["construction"]["count"] == 2

    @pytest.mark.asyncio
    async def test_run_eval_handles_answer_question_failure(self):
        """answer_questionが例外を投げてもrun_evalがゼロスコアで継続する。"""
        company_id = str(uuid4())
        dataset = [
            GoldenSample(
                question="失敗する質問",
                expected_keywords=["円"],
                category="construction",
                difficulty="easy",
            ),
        ]

        async def mock_fail(*args, **kwargs):
            raise RuntimeError("LLM error")

        with patch("brain.knowledge.eval.answer_question", side_effect=mock_fail):
            report = await run_eval(company_id=company_id, golden_dataset=dataset)

        assert report.total == 1
        assert report.avg_confidence == 0.0
        assert report.avg_keyword_hit_rate == 0.0

    @pytest.mark.asyncio
    async def test_by_difficulty_grouping(self):
        """difficulty別の集計が正しく行われることを確認。"""
        company_id = str(uuid4())
        dataset = [
            GoldenSample(question="Q1", expected_keywords=["A"], category="c", difficulty="easy"),
            GoldenSample(question="Q2", expected_keywords=["B"], category="c", difficulty="hard"),
            GoldenSample(question="Q3", expected_keywords=["C"], category="c", difficulty="easy"),
        ]

        call_idx = 0
        answers = ["Aが正解です", "関係ない回答", "Cが答えです"]

        async def mock_answer(question, company_id, **kwargs):
            nonlocal call_idx
            m = MagicMock()
            m.answer = answers[call_idx]
            m.confidence = 0.8
            m.cost_yen = 0.01
            m.search_mode = "enhanced"
            call_idx += 1
            return m

        with patch("brain.knowledge.eval.answer_question", side_effect=mock_answer):
            report = await run_eval(company_id=company_id, golden_dataset=dataset)

        assert report.by_difficulty["easy"]["count"] == 2
        assert report.by_difficulty["hard"]["count"] == 1
        # "easy"の2件: Q1("A"はヒット→1.0) + Q3("C"はヒット→1.0) → avg 1.0
        assert report.by_difficulty["easy"]["avg_keyword_hit_rate"] == 1.0
        # "hard"の1件: Q2("B"はヒットしない→0.0)
        assert report.by_difficulty["hard"]["avg_keyword_hit_rate"] == 0.0

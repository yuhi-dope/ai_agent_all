"""Tests for brain/inference/prompt_optimizer.py — 新規追加関数のテスト。

対象:
- analyze_rejections
- generate_prompt_improvement
- run_optimization_cycle
- PromptVersion / OptimizationResult データクラス
- 内部ヘルパー (_classify_rejection_patterns, _extract_proposed_changes)
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain.inference.prompt_optimizer import (
    OptimizationResult,
    PromptVersion,
    analyze_rejections,
    generate_prompt_improvement,
    run_optimization_cycle,
)


COMPANY_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_mock_db(rows: list[dict] | None = None) -> MagicMock:
    """Supabase クライアントのメソッドチェーンをモックする。"""
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.data = rows or []

    chain = mock_db.table.return_value
    for method in ("select", "eq", "gte", "lte", "neq", "execute"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    return mock_db


def _make_rejected_row(
    pipeline: str,
    steps: list[dict] | None = None,
    rejection_reason: str = "",
    exec_id: str | None = None,
) -> dict:
    """execution_logs の却下行を生成するヘルパー。"""
    ops: dict = {
        "pipeline": pipeline,
        "steps": steps or [],
        "input_data": "テスト入力データ",
    }
    return {
        "id": exec_id or str(uuid.uuid4()),
        "operations": ops,
        "approval_status": "rejected",
        "rejection_reason": rejection_reason,
        "created_at": "2026-03-20T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# TestAnalyzeRejections
# ---------------------------------------------------------------------------

class TestAnalyzeRejections:

    @pytest.mark.asyncio
    async def test_returns_rejection_analysis_with_patterns(self):
        """却下行から pipeline / step_name / rejection_count / rejection_patterns が正しく集計される。"""
        rows = [
            _make_rejected_row(
                "construction/estimation",
                steps=[{"step": "extract", "result": "出力A"}],
                rejection_reason="計算が間違っています",
            ),
            _make_rejected_row(
                "construction/estimation",
                steps=[{"step": "extract", "result": "出力B"}],
                rejection_reason="金額の計算ミスがあります",
            ),
            _make_rejected_row(
                "construction/estimation",
                steps=[{"step": "extract", "result": "出力C"}],
                rejection_reason="フォーマットが違います",
            ),
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert len(result) == 1
        item = result[0]
        assert item["pipeline"] == "construction/estimation"
        assert item["step_name"] == "extract"
        assert item["rejection_count"] == 3
        assert isinstance(item["rejection_patterns"], dict)
        # 「計算ミス」パターンが検出されること
        assert item["rejection_patterns"].get("計算ミス", 0) > 0

    @pytest.mark.asyncio
    async def test_company_id_filter_passed_to_db(self):
        """company_id が DB クエリの eq() に渡される。"""
        mock_db = _make_mock_db([])
        chain = mock_db.table.return_value

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await analyze_rejections(company_id=COMPANY_ID)

        # company_id と approval_status の両方で eq が呼ばれること
        eq_calls = [call.args for call in chain.eq.call_args_list]
        assert ("company_id", COMPANY_ID) in eq_calls
        assert ("approval_status", "rejected") in eq_calls

    @pytest.mark.asyncio
    async def test_pipeline_name_filter(self):
        """pipeline_name 指定時は対象パイプライン以外の行が除外される。"""
        rows = [
            _make_rejected_row(
                "construction/estimation",
                steps=[{"step": "extract", "result": "出力A"}],
                rejection_reason="計算ミス",
            ),
            _make_rejected_row(
                "manufacturing/quoting",
                steps=[{"step": "analyze", "result": "出力B"}],
                rejection_reason="精度不足",
            ),
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(
                company_id=COMPANY_ID,
                pipeline_name="construction/estimation",
            )

        assert len(result) == 1
        assert result[0]["pipeline"] == "construction/estimation"

    @pytest.mark.asyncio
    async def test_examples_capped_at_5(self):
        """各ステップの examples は最大5件。"""
        rows = [
            _make_rejected_row(
                "test/pipe",
                steps=[{"step": "step1", "result": f"出力{i}"}],
                rejection_reason="精度が低い",
            )
            for i in range(10)
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert len(result) == 1
        assert len(result[0]["examples"]) <= 5

    @pytest.mark.asyncio
    async def test_no_steps_uses_pipeline_as_single_step(self):
        """steps が空の行は __pipeline__ ステップとして集計される。"""
        rows = [
            _make_rejected_row(
                "common/expense",
                steps=[],
                rejection_reason="金額が違う",
            ),
        ]
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert len(result) == 1
        assert result[0]["step_name"] == "__pipeline__"

    @pytest.mark.asyncio
    async def test_empty_data_returns_empty_list(self):
        """却下ログがない場合は空リストを返す。"""
        mock_db = _make_mock_db([])

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_sorted_by_rejection_count_descending(self):
        """結果は却下件数の多い順（降順）でソートされる。"""
        rows = (
            [
                _make_rejected_row("pipe/a", steps=[{"step": "s1", "result": "x"}])
                for _ in range(3)
            ]
            + [
                _make_rejected_row("pipe/b", steps=[{"step": "s2", "result": "x"}])
                for _ in range(7)
            ]
        )
        mock_db = _make_mock_db(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        counts = [r["rejection_count"] for r in result]
        assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# TestGeneratePromptImprovement
# ---------------------------------------------------------------------------

class TestGeneratePromptImprovement:

    @pytest.mark.asyncio
    async def test_returns_optimization_result(self):
        """正常系: OptimizationResult が返される。"""
        mock_response = MagicMock()
        mock_response.content = (
            "改善点:\n- 出力フォーマットを明確化\n- 業界用語を追加\n\n"
            "```prompt\nImproved prompt text\n```"
        )
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        rejection_examples = [
            {"input": "見積書", "output": "不正確な出力", "rejection_reason": "計算ミスがあります"},
            {"input": "工事費", "output": "フォーマット違反", "rejection_reason": "フォーマットが違います"},
        ]

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await generate_prompt_improvement(
                prompt_key="construction_estimation_extract",
                current_prompt="現在のプロンプト",
                rejection_examples=rejection_examples,
            )

        assert isinstance(result, OptimizationResult)
        assert result.prompt_key == "construction_estimation_extract"
        assert result.rejection_count == 2
        assert result.new_prompt == "Improved prompt text"
        assert isinstance(result.rejection_patterns, dict)
        assert isinstance(result.proposed_changes, list)
        assert len(result.proposed_changes) > 0

    @pytest.mark.asyncio
    async def test_confidence_increases_with_more_rejections(self):
        """却下件数が多いほど confidence が高くなる。"""
        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        few_examples = [{"input": "x", "output": "y", "rejection_reason": "計算ミス"}]
        many_examples = [
            {"input": f"input{i}", "output": f"out{i}", "rejection_reason": "計算ミス"}
            for i in range(10)
        ]

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result_few = await generate_prompt_improvement("key1", "prompt", few_examples)
            result_many = await generate_prompt_improvement("key2", "prompt", many_examples)

        assert result_many.confidence >= result_few.confidence

    @pytest.mark.asyncio
    async def test_confidence_capped_at_1(self):
        """confidence は最大 1.0 を超えない。"""
        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        # 全パターン + 最大事例数でconfidenceが1を超えないこと
        examples = [
            {
                "input": f"in{i}",
                "output": f"out{i}",
                "rejection_reason": "計算ミスがありフォーマット違反で業界知識不足でした",
            }
            for i in range(20)
        ]

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await generate_prompt_improvement("key", "prompt", examples)

        assert result.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_llm_error_returns_result_with_none_prompt(self):
        """LLM呼び出し失敗時は new_prompt=None, confidence=0.0 の結果を返す。"""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM failure"))

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await generate_prompt_improvement(
                prompt_key="test_key",
                current_prompt="prompt",
                rejection_examples=[{"input": "x", "output": "y", "rejection_reason": "bad"}],
            )

        assert isinstance(result, OptimizationResult)
        assert result.new_prompt is None
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_rejection_patterns_classified_correctly(self):
        """「計算ミス」キーワードが patterns に計上される。"""
        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        examples = [
            {"input": "x", "output": "y", "rejection_reason": "金額の計算が間違っています"},
        ]

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            result = await generate_prompt_improvement("key", "prompt", examples)

        assert result.rejection_patterns.get("計算ミス", 0) > 0

    @pytest.mark.asyncio
    async def test_uses_fast_tier(self):
        """LLM は ModelTier.FAST で呼ばれる（Gemini Flash 優先）。"""
        from llm.client import ModelTier

        mock_response = MagicMock()
        mock_response.content = "```prompt\nNew prompt\n```"
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.inference.prompt_optimizer.get_llm_client", return_value=mock_llm):
            await generate_prompt_improvement("key", "prompt", [])

        call_args = mock_llm.generate.call_args[0][0]
        assert call_args.tier == ModelTier.FAST


# ---------------------------------------------------------------------------
# TestRunOptimizationCycle
# ---------------------------------------------------------------------------

class TestRunOptimizationCycle:

    @pytest.mark.asyncio
    async def test_returns_list_of_optimization_results(self):
        """最適化サイクルが OptimizationResult のリストを返す。"""
        mock_analyses = [
            {
                "pipeline": "construction/estimation",
                "step_name": "extract",
                "rejection_count": 8,
                "rejection_patterns": {"計算ミス": 3, "精度不足": 2, "フォーマット違反": 0, "業界知識不足": 0},
                "rejection_reasons": ["計算ミス", "精度不足"],
                "examples": [
                    {"input": "in1", "output": "out1", "rejection_reason": "計算ミス", "execution_id": "abc"},
                ],
            }
        ]

        mock_result = OptimizationResult(
            prompt_key="construction_estimation_extract",
            current_version=1,
            rejection_count=8,
            rejection_patterns={"計算ミス": 3, "精度不足": 2, "フォーマット違反": 0, "業界知識不足": 0},
            proposed_changes=["出力フォーマットを明確化"],
            new_prompt="改善されたプロンプト",
            confidence=0.75,
        )

        with (
            patch(
                "brain.inference.prompt_optimizer.analyze_rejections",
                AsyncMock(return_value=mock_analyses),
            ),
            patch(
                "brain.inference.prompt_optimizer.generate_prompt_improvement",
                AsyncMock(return_value=mock_result),
            ),
            patch(
                "brain.inference.prompt_optimizer._read_current_prompt",
                return_value="現在のプロンプト",
            ),
        ):
            results = await run_optimization_cycle(company_id=COMPANY_ID)

        assert len(results) == 1
        assert results[0].prompt_key == "construction_estimation_extract"
        assert results[0].rejection_count == 8

    @pytest.mark.asyncio
    async def test_skips_steps_below_min_rejections(self):
        """min_rejections 未満の却下件数のステップはスキップされる。"""
        mock_analyses = [
            {
                "pipeline": "test/pipe",
                "step_name": "step1",
                "rejection_count": 3,  # min_rejections=5 に満たない
                "rejection_patterns": {},
                "rejection_reasons": [],
                "examples": [],
            }
        ]

        with (
            patch(
                "brain.inference.prompt_optimizer.analyze_rejections",
                AsyncMock(return_value=mock_analyses),
            ),
        ):
            results = await run_optimization_cycle(company_id=COMPANY_ID, min_rejections=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_min_rejections_default_is_5(self):
        """デフォルトの min_rejections は 5。"""
        mock_analyses = [
            {
                "pipeline": "pipe/a",
                "step_name": "s1",
                "rejection_count": 4,  # 5未満
                "rejection_patterns": {},
                "rejection_reasons": [],
                "examples": [],
            },
            {
                "pipeline": "pipe/b",
                "step_name": "s2",
                "rejection_count": 5,  # 5以上（対象）
                "rejection_patterns": {},
                "rejection_reasons": [],
                "examples": [],
            },
        ]

        mock_result = OptimizationResult(
            prompt_key="pipe_b_s2",
            current_version=1,
            rejection_count=5,
            rejection_patterns={},
            proposed_changes=[],
            new_prompt=None,
            confidence=0.0,
        )

        with (
            patch(
                "brain.inference.prompt_optimizer.analyze_rejections",
                AsyncMock(return_value=mock_analyses),
            ),
            patch(
                "brain.inference.prompt_optimizer.generate_prompt_improvement",
                AsyncMock(return_value=mock_result),
            ),
            patch(
                "brain.inference.prompt_optimizer._read_current_prompt",
                return_value="prompt",
            ),
        ):
            results = await run_optimization_cycle(company_id=COMPANY_ID)  # min_rejections デフォルト

        assert len(results) == 1
        assert results[0].prompt_key == "pipe_b_s2"

    @pytest.mark.asyncio
    async def test_empty_analyses_returns_empty_list(self):
        """却下分析が空の場合、空リストを返す。"""
        with patch(
            "brain.inference.prompt_optimizer.analyze_rejections",
            AsyncMock(return_value=[]),
        ):
            results = await run_optimization_cycle(company_id=COMPANY_ID)

        assert results == []

    @pytest.mark.asyncio
    async def test_prompt_key_replaces_slashes(self):
        """prompt_key のスラッシュはアンダースコアに変換される。"""
        mock_analyses = [
            {
                "pipeline": "construction/estimation",
                "step_name": "extract",
                "rejection_count": 10,
                "rejection_patterns": {},
                "rejection_reasons": [],
                "examples": [],
            }
        ]

        captured_key: list[str] = []

        async def _mock_generate(prompt_key: str, **kwargs: object) -> OptimizationResult:
            captured_key.append(prompt_key)
            return OptimizationResult(
                prompt_key=prompt_key,
                current_version=1,
                rejection_count=10,
                rejection_patterns={},
                proposed_changes=[],
                new_prompt=None,
                confidence=0.5,
            )

        with (
            patch(
                "brain.inference.prompt_optimizer.analyze_rejections",
                AsyncMock(return_value=mock_analyses),
            ),
            patch(
                "brain.inference.prompt_optimizer.generate_prompt_improvement",
                _mock_generate,
            ),
            patch(
                "brain.inference.prompt_optimizer._read_current_prompt",
                return_value="prompt",
            ),
        ):
            await run_optimization_cycle(company_id=COMPANY_ID)

        assert len(captured_key) == 1
        assert "/" not in captured_key[0]
        assert captured_key[0] == "construction_estimation_extract"


# ---------------------------------------------------------------------------
# TestDataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:

    def test_prompt_version_creation(self):
        """PromptVersion が正しく生成される。"""
        pv = PromptVersion(
            prompt_key="construction_estimation_extract",
            version=2,
            prompt_text="改善されたプロンプト",
            created_at="2026-03-30T00:00:00Z",
            accuracy_before=0.65,
            accuracy_after=0.88,
            feedback_summary="計算ミスパターンへの対応",
        )
        assert pv.prompt_key == "construction_estimation_extract"
        assert pv.version == 2
        assert pv.accuracy_after == 0.88

    def test_optimization_result_creation(self):
        """OptimizationResult が正しく生成される。"""
        result = OptimizationResult(
            prompt_key="manufacturing_quoting_analyze",
            current_version=1,
            rejection_count=7,
            rejection_patterns={"計算ミス": 5, "精度不足": 2, "フォーマット違反": 0, "業界知識不足": 0},
            proposed_changes=["数値計算ステップを明示", "出力形式をJSONに固定"],
            new_prompt="改善版プロンプト本文",
            confidence=0.82,
        )
        assert result.rejection_count == 7
        assert result.rejection_patterns["計算ミス"] == 5
        assert result.confidence == 0.82
        assert result.new_prompt == "改善版プロンプト本文"

    def test_optimization_result_none_new_prompt(self):
        """new_prompt は None も許容される。"""
        result = OptimizationResult(
            prompt_key="test_key",
            current_version=1,
            rejection_count=0,
            rejection_patterns={},
            proposed_changes=[],
            new_prompt=None,
            confidence=0.0,
        )
        assert result.new_prompt is None


# ---------------------------------------------------------------------------
# TestInternalHelpers
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    """内部ヘルパー関数のテスト。"""

    def test_classify_rejection_patterns_calculates(self):
        """計算ミスキーワードが含まれるテキストで「計算ミス」が計上される。"""
        from brain.inference.prompt_optimizer import _classify_rejection_patterns
        patterns = _classify_rejection_patterns("金額の計算が間違っています")
        assert patterns["計算ミス"] > 0

    def test_classify_rejection_patterns_format(self):
        """フォーマット違反キーワードが含まれる場合「フォーマット違反」が計上される。"""
        from brain.inference.prompt_optimizer import _classify_rejection_patterns
        patterns = _classify_rejection_patterns("出力フォーマットが指定と異なります")
        assert patterns["フォーマット違反"] > 0

    def test_classify_rejection_patterns_multiple(self):
        """複数パターンが同時に検出される。"""
        from brain.inference.prompt_optimizer import _classify_rejection_patterns
        patterns = _classify_rejection_patterns(
            "計算が間違っていて、フォーマットも違う"
        )
        assert patterns["計算ミス"] > 0
        assert patterns["フォーマット違反"] > 0

    def test_classify_rejection_patterns_empty_text(self):
        """空テキストでは全パターンが 0。"""
        from brain.inference.prompt_optimizer import _classify_rejection_patterns
        patterns = _classify_rejection_patterns("")
        assert all(v == 0 for v in patterns.values())

    def test_extract_proposed_changes_from_bullet_list(self):
        """「改善点:」以降の箇条書きが抽出される。"""
        from brain.inference.prompt_optimizer import _extract_proposed_changes
        content = (
            "改善点:\n"
            "- 出力フォーマットを明確化する\n"
            "- 業界用語の説明を追加する\n"
            "- エッジケースへの対応を追加する\n\n"
            "以上です。"
        )
        changes = _extract_proposed_changes(content)
        assert len(changes) >= 1
        assert any("フォーマット" in c for c in changes)

    def test_extract_proposed_changes_fallback_to_summary(self):
        """「改善点:」パターンがない場合は内容の先頭200文字を返す。"""
        from brain.inference.prompt_optimizer import _extract_proposed_changes
        content = "プロンプトを大幅に改善しました。具体的には出力フォーマットを変更しました。"
        changes = _extract_proposed_changes(content)
        assert len(changes) == 1
        assert "プロンプト" in changes[0]

    def test_extract_prompt_block_with_prompt_fence(self):
        """```prompt...``` ブロックから中身を抽出する。"""
        from brain.inference.prompt_optimizer import _extract_prompt_block
        content = "改善案:\n```prompt\nExtracted prompt here\n```\n以上"
        assert _extract_prompt_block(content) == "Extracted prompt here"

    def test_extract_prompt_block_with_generic_fence(self):
        """```...``` ブロック（prompt 指定なし）から中身を抽出する。"""
        from brain.inference.prompt_optimizer import _extract_prompt_block
        content = "提案:\n```\nGeneric prompt\n```"
        assert _extract_prompt_block(content) == "Generic prompt"

    def test_extract_prompt_block_no_fence_returns_full(self):
        """コードブロックがない場合はテキスト全体を返す。"""
        from brain.inference.prompt_optimizer import _extract_prompt_block
        content = "改善プロンプトです。"
        assert _extract_prompt_block(content) == "改善プロンプトです。"

    def test_extract_prompt_block_empty_returns_none(self):
        """空テキストは None を返す。"""
        from brain.inference.prompt_optimizer import _extract_prompt_block
        assert _extract_prompt_block("   ") is None

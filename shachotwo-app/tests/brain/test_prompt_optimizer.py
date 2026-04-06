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
    get_active_prompt,
    rollback_prompt_version,
    run_optimization_cycle,
    save_prompt_version,
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
    for method in ("select", "eq", "gte", "lte", "neq", "order", "limit", "execute"):
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


# ---------------------------------------------------------------------------
# TestSavePromptVersion
# ---------------------------------------------------------------------------

class TestSavePromptVersion:

    def _make_db(
        self,
        deactivate_rows: list | None = None,
        version_rows: list | None = None,
        insert_row: dict | None = None,
    ) -> MagicMock:
        """save_prompt_version が使うDBメソッドチェーンをモックする。"""
        mock_db = MagicMock()

        # update チェーン（既存アクティブを非アクティブ化）
        update_chain = MagicMock()
        update_chain.update.return_value = update_chain
        update_chain.eq.return_value = update_chain
        update_chain.is_.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=deactivate_rows or [])

        # select チェーン（max version 取得）
        version_result = MagicMock()
        version_result.data = version_rows or []
        select_chain = MagicMock()
        select_chain.select.return_value = select_chain
        select_chain.eq.return_value = select_chain
        select_chain.is_.return_value = select_chain
        select_chain.order.return_value = select_chain
        select_chain.limit.return_value = select_chain
        select_chain.execute.return_value = version_result

        # insert チェーン
        insert_result = MagicMock()
        insert_result.data = [insert_row or {"id": "new-uuid-1234"}]
        insert_chain = MagicMock()
        insert_chain.insert.return_value = insert_chain
        insert_chain.execute.return_value = insert_result

        # table() の呼び出し順序に応じてチェーンを切り替える
        call_count = {"n": 0}

        def _table_side_effect(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                # 1回目: update (deactivate)
                return update_chain
            elif n == 2:
                # 2回目: select (max version)
                return select_chain
            else:
                # 3回目: insert
                return insert_chain

        mock_db.table.side_effect = _table_side_effect
        return mock_db

    @pytest.mark.asyncio
    async def test_returns_new_id(self):
        """正常系: 新しいレコードのIDが返される。"""
        mock_db = self._make_db(
            version_rows=[{"version": 2}],
            insert_row={"id": "abc-123"},
        )
        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            new_id = await save_prompt_version(
                pipeline="construction/estimation",
                step_name="extract",
                prompt_text="新しいプロンプト",
                accuracy_before=0.65,
            )
        assert new_id == "abc-123"

    @pytest.mark.asyncio
    async def test_version_increments_from_max(self):
        """max(version)+1 で新バージョン番号が決定される。"""
        inserted_data: list[dict] = []

        mock_db = MagicMock()
        call_count = {"n": 0}

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()
            if n == 1:
                # update (deactivate)
                chain.update.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.execute.return_value = MagicMock(data=[])
            elif n == 2:
                # select (max version) → version=3 を返す
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = MagicMock(data=[{"version": 3}])
            else:
                # insert → 挿入データをキャプチャ
                def _insert(data: dict) -> MagicMock:
                    inserted_data.append(data)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock(data=[{"id": "new-id"}])
                    return inner

                chain.insert = _insert
            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await save_prompt_version(
                pipeline="test/pipe",
                step_name="step1",
                prompt_text="prompt text",
            )

        assert len(inserted_data) == 1
        assert inserted_data[0]["version"] == 4  # 3 + 1

    @pytest.mark.asyncio
    async def test_first_version_is_1_when_no_existing(self):
        """既存バージョンがない場合は version=1 で INSERT される。"""
        inserted_data: list[dict] = []
        mock_db = MagicMock()
        call_count = {"n": 0}

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()
            if n == 1:
                chain.update.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.execute.return_value = MagicMock(data=[])
            elif n == 2:
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = MagicMock(data=[])  # 既存なし
            else:
                def _insert(data: dict) -> MagicMock:
                    inserted_data.append(data)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock(data=[{"id": "new-id"}])
                    return inner
                chain.insert = _insert
            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await save_prompt_version(
                pipeline="test/pipe",
                step_name="step1",
                prompt_text="最初のプロンプト",
            )

        assert inserted_data[0]["version"] == 1

    @pytest.mark.asyncio
    async def test_is_active_true_on_new_record(self):
        """新規レコードは is_active=True で挿入される。"""
        inserted_data: list[dict] = []
        mock_db = MagicMock()
        call_count = {"n": 0}

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()
            if n == 1:
                chain.update.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.execute.return_value = MagicMock(data=[])
            elif n == 2:
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = MagicMock(data=[{"version": 1}])
            else:
                def _insert(data: dict) -> MagicMock:
                    inserted_data.append(data)
                    inner = MagicMock()
                    inner.execute.return_value = MagicMock(data=[{"id": "x"}])
                    return inner
                chain.insert = _insert
            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await save_prompt_version("p", "s", "prompt")

        assert inserted_data[0]["is_active"] is True


# ---------------------------------------------------------------------------
# TestRollbackPromptVersion
# ---------------------------------------------------------------------------

class TestRollbackPromptVersion:

    @pytest.mark.asyncio
    async def test_returns_false_when_no_active_version(self):
        """アクティブバージョンがない場合は False を返す。"""
        mock_db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = MagicMock(data=None)
        mock_db.table.return_value = chain

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await rollback_prompt_version("test/pipe", "step1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_already_version_1(self):
        """version=1 の場合はロールバック不可で False を返す。"""
        mock_db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = MagicMock(data={"id": "v1-id", "version": 1})
        mock_db.table.return_value = chain

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await rollback_prompt_version("test/pipe", "step1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_successful_rollback(self):
        """正常系: ロールバック成功で True を返す。"""
        mock_db = MagicMock()
        call_count = {"n": 0}
        update_calls: list[dict] = []

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()

            if n == 1:
                # 1回目: 現在のアクティブバージョン取得 (version=3)
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.maybe_single.return_value = chain
                chain.execute.return_value = MagicMock(
                    data={"id": "v3-id", "version": 3}
                )
            elif n == 2:
                # 2回目: 前バージョン取得 (version=2)
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.is_.return_value = chain
                chain.maybe_single.return_value = chain
                chain.execute.return_value = MagicMock(data={"id": "v2-id"})
            else:
                # 3・4回目: update (deactivate current / activate prev)
                def _update(data: dict) -> MagicMock:
                    update_calls.append(data)
                    inner = MagicMock()
                    inner.eq.return_value = inner
                    inner.execute.return_value = MagicMock(data=[])
                    return inner
                chain.update = _update

            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await rollback_prompt_version("test/pipe", "step1")

        assert result is True
        # deactivate と activate の update が呼ばれること
        assert any(d == {"is_active": False} for d in update_calls)
        assert any(d == {"is_active": True} for d in update_calls)

    @pytest.mark.asyncio
    async def test_returns_false_when_prev_version_not_found(self):
        """前バージョンのレコードが存在しない場合は False を返す。"""
        mock_db = MagicMock()
        call_count = {"n": 0}

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.is_.return_value = chain
            chain.maybe_single.return_value = chain

            if n == 1:
                chain.execute.return_value = MagicMock(
                    data={"id": "v2-id", "version": 2}
                )
            else:
                chain.execute.return_value = MagicMock(data=None)  # 前バージョンなし
            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await rollback_prompt_version("test/pipe", "step1")

        assert result is False


# ---------------------------------------------------------------------------
# TestGetActivePrompt
# ---------------------------------------------------------------------------

class TestGetActivePrompt:

    def _make_db(self, text: str | None) -> MagicMock:
        mock_db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.maybe_single.return_value = chain
        data = {"prompt_text": text} if text is not None else None
        chain.execute.return_value = MagicMock(data=data)
        mock_db.table.return_value = chain
        return mock_db

    @pytest.mark.asyncio
    async def test_returns_prompt_text_when_found(self):
        """アクティブなプロンプトテキストが返される。"""
        mock_db = self._make_db("アクティブなプロンプト")
        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await get_active_prompt("construction/estimation", "extract")
        assert result == "アクティブなプロンプト"

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        """該当レコードがない場合は None を返す。"""
        mock_db = self._make_db(None)
        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await get_active_prompt("test/pipe", "step1")
        assert result is None

    @pytest.mark.asyncio
    async def test_company_id_filter_applied(self):
        """company_id 指定時は eq(company_id) が呼ばれる。"""
        mock_db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = MagicMock(data={"prompt_text": "個社プロンプト"})
        mock_db.table.return_value = chain

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await get_active_prompt("test/pipe", "step1", company_id=COMPANY_ID)

        assert result == "個社プロンプト"
        eq_calls = [call.args for call in chain.eq.call_args_list]
        assert ("company_id", COMPANY_ID) in eq_calls

    @pytest.mark.asyncio
    async def test_fallback_to_global_when_company_not_found(self):
        """個社設定がない場合は全社共通（company_id IS NULL）にフォールバックする。"""
        mock_db = MagicMock()
        call_count = {"n": 0}

        def _table(table_name: str) -> MagicMock:
            call_count["n"] += 1
            n = call_count["n"]
            chain = MagicMock()
            chain.select.return_value = chain
            chain.eq.return_value = chain
            chain.is_.return_value = chain
            chain.maybe_single.return_value = chain

            if n == 1:
                # 1回目 (個社): データなし
                chain.execute.return_value = MagicMock(data=None)
            else:
                # 2回目 (全社共通): データあり
                chain.execute.return_value = MagicMock(
                    data={"prompt_text": "全社共通プロンプト"}
                )
            return chain

        mock_db.table.side_effect = _table

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await get_active_prompt("test/pipe", "step1", company_id=COMPANY_ID)

        assert result == "全社共通プロンプト"

    @pytest.mark.asyncio
    async def test_no_company_id_queries_global_only(self):
        """company_id=None の場合は全社共通のみ1回クエリする。"""
        mock_db = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.is_.return_value = chain
        chain.maybe_single.return_value = chain
        chain.execute.return_value = MagicMock(data={"prompt_text": "共通プロンプト"})
        mock_db.table.return_value = chain

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await get_active_prompt("test/pipe", "step1", company_id=None)

        assert result == "共通プロンプト"
        # table() は1回のみ呼ばれる
        assert mock_db.table.call_count == 1


# ---------------------------------------------------------------------------
# TestAnalyzeRejectionsNewFeatures (修正仕様のテスト)
# ---------------------------------------------------------------------------

class TestAnalyzeRejectionsNewFeatures:
    """修正後の analyze_rejections: feedback_type フィルタ・limit・重複排除テスト。"""

    def _make_db_with_rows(self, rows: list[dict]) -> MagicMock:
        """order/limit チェーンを含むDBモックを生成する。"""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.data = rows

        chain = mock_db.table.return_value
        for method in ("select", "eq", "gte", "order", "limit", "execute"):
            getattr(chain, method).return_value = chain
        chain.execute.return_value = mock_result
        return mock_db

    @pytest.mark.asyncio
    async def test_feedback_type_null_is_included(self):
        """feedback_type=NULL の行は prompt_improvement_only と同じ扱いで含まれる。"""
        rows = [
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "construction/estimation",
                    "steps": [{"step": "extract", "result": "出力"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "計算ミス",
                "feedback_type": None,  # NULL
                "created_at": "2026-03-20T00:00:00Z",
            }
        ]
        mock_db = self._make_db_with_rows(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert len(result) == 1
        assert result[0]["rejection_count"] == 1

    @pytest.mark.asyncio
    async def test_feedback_type_prompt_improvement_only_is_included(self):
        """feedback_type='prompt_improvement_only' の行は含まれる。"""
        rows = [
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "test/pipe",
                    "steps": [{"step": "s1", "result": "出力"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "精度不足",
                "feedback_type": "prompt_improvement_only",
                "created_at": "2026-03-20T00:00:00Z",
            }
        ]
        mock_db = self._make_db_with_rows(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_feedback_type_rule_candidate_is_excluded(self):
        """feedback_type='rule_candidate' の行はプロンプト改善対象外で除外される。"""
        rows = [
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "test/pipe",
                    "steps": [{"step": "s1", "result": "出力"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "ルール候補",
                "feedback_type": "rule_candidate",
                "created_at": "2026-03-20T00:00:00Z",
            }
        ]
        mock_db = self._make_db_with_rows(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_feedback_types_only_includes_valid(self):
        """rule_candidate は除外し、NULL と prompt_improvement_only のみ集計される。"""
        rows = [
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "test/pipe",
                    "steps": [{"step": "s1", "result": "A"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "精度不足",
                "feedback_type": None,
                "created_at": "2026-03-20T01:00:00Z",
            },
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "test/pipe",
                    "steps": [{"step": "s1", "result": "B"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "計算ミス",
                "feedback_type": "prompt_improvement_only",
                "created_at": "2026-03-20T02:00:00Z",
            },
            {
                "id": str(uuid.uuid4()),
                "operations": {
                    "pipeline": "test/pipe",
                    "steps": [{"step": "s1", "result": "C"}],
                    "input_data": "入力",
                },
                "approval_status": "rejected",
                "rejection_reason": "ルール適用",
                "feedback_type": "rule_candidate",
                "created_at": "2026-03-20T03:00:00Z",
            },
        ]
        mock_db = self._make_db_with_rows(rows)

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            result = await analyze_rejections(company_id=COMPANY_ID)

        # rule_candidate を除いた2件のみ集計（ただし重複排除後は2件なのでrejection_count=2）
        assert len(result) == 1
        assert result[0]["rejection_count"] == 2

    @pytest.mark.asyncio
    async def test_duplicate_rejection_reasons_deduplicated(self):
        """同じ rejection_reason の行は代表1件（最新）だけ残される。"""
        from brain.inference.prompt_optimizer import _deduplicate_by_rejection_pattern
        rows = [
            {
                "id": "1",
                "rejection_reason": "計算ミスがあります",
                "feedback_type": None,
                "created_at": "2026-03-20T03:00:00Z",  # 最新
            },
            {
                "id": "2",
                "rejection_reason": "計算ミスがあります",  # 同じ理由
                "feedback_type": None,
                "created_at": "2026-03-20T01:00:00Z",
            },
            {
                "id": "3",
                "rejection_reason": "フォーマットが違います",  # 別の理由
                "feedback_type": None,
                "created_at": "2026-03-20T02:00:00Z",
            },
        ]
        result = _deduplicate_by_rejection_pattern(rows)

        assert len(result) == 2
        # 最初に現れた行（最新順）が残る
        remaining_ids = [r["id"] for r in result]
        assert "1" in remaining_ids
        assert "3" in remaining_ids
        assert "2" not in remaining_ids  # 重複として排除

    def test_deduplicate_empty_reason_not_deduplicated(self):
        """rejection_reason が空の行は重複判定せず全件残す。"""
        from brain.inference.prompt_optimizer import _deduplicate_by_rejection_pattern
        rows = [
            {"id": "1", "rejection_reason": "", "created_at": "2026-03-20T01:00:00Z"},
            {"id": "2", "rejection_reason": "", "created_at": "2026-03-20T02:00:00Z"},
            {"id": "3", "rejection_reason": None, "created_at": "2026-03-20T03:00:00Z"},
        ]
        result = _deduplicate_by_rejection_pattern(rows)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_limit_50_passed_to_query(self):
        """クエリに limit(50) が呼ばれることを確認する。"""
        mock_db = self._make_db_with_rows([])
        chain = mock_db.table.return_value

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await analyze_rejections(company_id=COMPANY_ID)

        limit_calls = [call.args for call in chain.limit.call_args_list]
        assert (50,) in limit_calls

    @pytest.mark.asyncio
    async def test_order_by_created_at_desc(self):
        """クエリに order("created_at", desc=True) が呼ばれることを確認する。"""
        mock_db = self._make_db_with_rows([])
        chain = mock_db.table.return_value

        with patch("brain.inference.prompt_optimizer.get_service_client", return_value=mock_db):
            await analyze_rejections(company_id=COMPANY_ID)

        order_calls = chain.order.call_args_list
        assert any(
            call.args[0] == "created_at" and call.kwargs.get("desc") is True
            for call in order_calls
        )

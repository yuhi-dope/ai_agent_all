"""Tests for brain/visualization/flow_generator.py — generate_flow_diagram のテスト。

generate_process_flow() のテストは test_visualization.py でカバー済み。
このファイルでは generate_flow_diagram + FlowDiagramResult / PipelineStep モデルをテストする。
"""
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from brain.visualization.flow_generator import (
    FlowDiagramResult,
    PipelineStep,
    _build_fallback_flow_mermaid,
    _build_flow_context,
    _extract_step_statuses,
    _get_pipeline_definition,
    _parse_flow_result,
    generate_flow_diagram,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_execution_id() -> str:
    return str(uuid4())


def _make_company_id() -> str:
    return str(uuid4())


def _make_execution_log(execution_id: str, company_id: str, success: bool = True) -> dict:
    return {
        "id": execution_id,
        "company_id": company_id,
        "overall_success": success,
        "operations": {
            "steps": [
                {"id": "S1", "name": "ドキュメント受信", "status": "completed"},
                {"id": "S2", "name": "LLM分析", "status": "completed"},
                {"id": "H1", "name": "人間承認", "status": "completed", "requires_human": True},
                {"id": "S3", "name": "見積書生成", "status": "completed"},
            ]
        },
    }


def _make_supabase_mock(logs: list[dict]) -> MagicMock:
    """execution_logs を返すSupabaseモックを生成する。"""
    mock_db = MagicMock()

    result = MagicMock()
    result.data = logs

    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.side_effect = lambda *a, **kw: chain
    chain.limit.return_value = chain
    chain.execute.return_value = result

    mock_db.table.return_value = chain
    return mock_db


MOCK_FLOW_RESPONSE = json.dumps({
    "mermaid": (
        "flowchart TD\n"
        '    S0(["ドキュメント受信"]) --> S1\n'
        '    S1["OCR・テキスト抽出"] --> S2\n'
        '    S2["LLM項目抽出"] --> H1\n'
        '    H1[["人間承認（見積確認）"]] --> S3\n'
        '    S3["見積書生成"] --> END\n'
        '    END(["完了"])\n'
        "    style H1 fill:#FFD700,stroke:#B8860B"
    ),
    "steps": [
        {"id": "S0", "name": "ドキュメント受信", "step_type": "start", "next_steps": ["S1"]},
        {"id": "S1", "name": "OCR・テキスト抽出", "step_type": "action", "next_steps": ["S2"]},
        {"id": "S2", "name": "LLM項目抽出", "step_type": "action", "next_steps": ["H1"]},
        {"id": "H1", "name": "人間承認（見積確認）", "step_type": "hitl", "next_steps": ["S3"]},
        {"id": "S3", "name": "見積書生成", "step_type": "action", "next_steps": ["END"]},
        {"id": "END", "name": "完了", "step_type": "end", "next_steps": []},
    ],
    "style_directives": [
        "style S1 fill:#90EE90,stroke:#228B22",
        "style S2 fill:#90EE90,stroke:#228B22",
    ],
})


# ---------------------------------------------------------------------------
# TestPipelineStepModel
# ---------------------------------------------------------------------------

class TestPipelineStepModel:

    def test_pipeline_step_valid(self):
        """PipelineStep が正しく構築される。"""
        step = PipelineStep(
            id="S1",
            name="テストステップ",
            step_type="action",
            next_steps=["S2"],
        )
        assert step.id == "S1"
        assert step.step_type == "action"
        assert step.next_steps == ["S2"]

    def test_default_step_type(self):
        """step_type のデフォルトは 'action'。"""
        step = PipelineStep(id="S1", name="テスト")
        assert step.step_type == "action"

    def test_hitl_step_type(self):
        """HITLステップが正しく設定される。"""
        step = PipelineStep(id="H1", name="人間承認", step_type="hitl")
        assert step.step_type == "hitl"


# ---------------------------------------------------------------------------
# TestFlowDiagramResultModel
# ---------------------------------------------------------------------------

class TestFlowDiagramResultModel:

    def test_flow_diagram_result_valid(self):
        """FlowDiagramResult が正しく構築される。"""
        result = FlowDiagramResult(
            mermaid="flowchart TD\n    A[テスト]",
            pipeline_name="test_pipeline",
        )
        assert result.pipeline_name == "test_pipeline"
        assert result.total_steps == 0
        assert result.completed_steps == 0

    def test_flow_diagram_serializable(self):
        """FlowDiagramResult が JSON シリアライズ可能。"""
        result = FlowDiagramResult(
            mermaid="flowchart TD\n    A[テスト]",
            pipeline_name="test_pipeline",
            step_statuses={"S1": "completed"},
        )
        dumped = result.model_dump()
        serialized = json.dumps(dumped, ensure_ascii=False)
        assert "test_pipeline" in serialized


# ---------------------------------------------------------------------------
# TestExtractStepStatuses
# ---------------------------------------------------------------------------

class TestExtractStepStatuses:

    def test_extract_completed_steps(self):
        """completedステータスが正しく抽出される。"""
        operations = {
            "steps": [
                {"id": "S1", "name": "ステップ1", "status": "completed"},
                {"id": "S2", "name": "ステップ2", "status": "running"},
            ]
        }
        statuses = _extract_step_statuses(operations, True)
        assert statuses.get("S1") == "completed"
        assert statuses.get("S2") == "running"

    def test_extract_hitl_steps(self):
        """requires_humanフラグでHITLとして検出される。"""
        operations = {
            "steps": [
                {"id": "H1", "name": "承認", "status": "pending", "requires_human": True},
            ]
        }
        statuses = _extract_step_statuses(operations, None)
        assert statuses.get("H1") == "hitl"

    def test_empty_operations_returns_empty(self):
        """空のoperationsは空の辞書を返す。"""
        statuses = _extract_step_statuses({}, None)
        assert statuses == {}

    def test_non_dict_operations_returns_empty(self):
        """dictでないoperationsは空の辞書を返す（クラッシュしない）。"""
        statuses = _extract_step_statuses("not a dict", None)  # type: ignore
        assert statuses == {}


# ---------------------------------------------------------------------------
# TestGetPipelineDefinition
# ---------------------------------------------------------------------------

class TestGetPipelineDefinition:

    def test_known_pipeline_returns_definition(self):
        """既知のパイプライン名で定義が返る。"""
        steps = _get_pipeline_definition("estimation_pipeline")
        assert len(steps) > 0
        # HITLステップが含まれること
        hitl_steps = [s for s in steps if s.get("step_type") == "hitl"]
        assert len(hitl_steps) >= 1

    def test_unknown_pipeline_returns_default(self):
        """未知のパイプライン名はデフォルト定義を返す。"""
        steps = _get_pipeline_definition("unknown_pipeline_xyz")
        assert len(steps) > 0

    def test_partial_match_returns_definition(self):
        """部分一致でも定義が返る。"""
        steps = _get_pipeline_definition("some_billing_pipeline")
        assert len(steps) > 0


# ---------------------------------------------------------------------------
# TestBuildFlowContext
# ---------------------------------------------------------------------------

class TestBuildFlowContext:

    def test_context_contains_pipeline_name(self):
        """コンテキストにパイプライン名が含まれる。"""
        steps = [{"id": "S1", "name": "テスト", "step_type": "action", "next_steps": []}]
        context = _build_flow_context("test_pipeline", steps, {})
        assert "test_pipeline" in context

    def test_context_contains_step_names(self):
        """コンテキストにステップ名が含まれる。"""
        steps = [{"id": "S1", "name": "ドキュメント受信", "step_type": "action", "next_steps": []}]
        context = _build_flow_context("test", steps, {})
        assert "ドキュメント受信" in context

    def test_context_contains_step_statuses(self):
        """実行状態がある場合、コンテキストに含まれる。"""
        steps = [{"id": "S1", "name": "テスト", "step_type": "action", "next_steps": []}]
        statuses = {"S1": "completed", "H1": "hitl"}
        context = _build_flow_context("test", steps, statuses)
        assert "completed" in context
        assert "hitl" in context


# ---------------------------------------------------------------------------
# TestParseFlowResult
# ---------------------------------------------------------------------------

class TestParseFlowResult:

    def test_parse_valid_response(self):
        """正常なLLMレスポンスをFlowDiagramResultにパースできる。"""
        result = _parse_flow_result("estimation_pipeline", MOCK_FLOW_RESPONSE, {})
        assert result.pipeline_name == "estimation_pipeline"
        assert result.total_steps == 6
        assert "flowchart" in result.mermaid.lower()

    def test_parse_with_style_directives(self):
        """style_directives が Mermaid に追加される。"""
        result = _parse_flow_result("test", MOCK_FLOW_RESPONSE, {})
        assert "fill:#90EE90" in result.mermaid

    def test_parse_hitl_in_mermaid(self):
        """HITLノードの黄色スタイルがMermaidに含まれる。"""
        result = _parse_flow_result("test", MOCK_FLOW_RESPONSE, {})
        assert "FFD700" in result.mermaid

    def test_parse_invalid_json_returns_fallback(self):
        """無効なJSONはフォールバックMermaidを返す（クラッシュしない）。"""
        result = _parse_flow_result("test_pipeline", "これはJSONではありません", {})
        assert isinstance(result, FlowDiagramResult)
        assert "flowchart" in result.mermaid.lower()

    def test_completed_steps_counted(self):
        """completed状態のステップ数が正しくカウントされる。"""
        step_statuses = {"S1": "completed", "S2": "completed", "H1": "hitl"}
        result = _parse_flow_result("test", MOCK_FLOW_RESPONSE, step_statuses)
        assert result.completed_steps == 2


# ---------------------------------------------------------------------------
# TestBuildFallbackFlowMermaid
# ---------------------------------------------------------------------------

class TestBuildFallbackFlowMermaid:

    def test_fallback_empty_steps(self):
        """ステップが空の場合フォールバックメッセージを返す。"""
        mermaid = _build_fallback_flow_mermaid([], {})
        assert "フロー情報がありません" in mermaid

    def test_fallback_includes_start_end_with_rounded(self):
        """start/end ノードが丸括弧（角丸）で表現される。"""
        steps = [
            {"id": "S0", "name": "開始", "step_type": "start", "next_steps": ["S1"]},
            {"id": "END", "name": "完了", "step_type": "end", "next_steps": []},
        ]
        mermaid = _build_fallback_flow_mermaid(steps, {})
        assert "開始" in mermaid
        assert "完了" in mermaid

    def test_fallback_hitl_has_yellow_style(self):
        """HITLノードに黄色スタイルが適用される。"""
        steps = [{"id": "H1", "name": "人間承認", "step_type": "hitl", "next_steps": []}]
        mermaid = _build_fallback_flow_mermaid(steps, {})
        assert "FFD700" in mermaid

    def test_fallback_status_color_applied(self):
        """実行状態に応じた色が適用される。"""
        steps = [{"id": "S1", "name": "完了ステップ", "step_type": "action", "next_steps": []}]
        statuses = {"S1": "completed"}
        mermaid = _build_fallback_flow_mermaid(steps, statuses)
        # 緑色（completed）のスタイル
        assert "90EE90" in mermaid


# ---------------------------------------------------------------------------
# TestGenerateFlowDiagram
# ---------------------------------------------------------------------------

class TestGenerateFlowDiagram:

    @pytest.mark.asyncio
    async def test_returns_flow_diagram_result(self):
        """generate_flow_diagram が FlowDiagramResult を返す。"""
        execution_id = _make_execution_id()
        company_id = _make_company_id()
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.04
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            result = await generate_flow_diagram("estimation_pipeline", execution_id, mock_db)

        assert isinstance(result, FlowDiagramResult)
        assert result.pipeline_name == "estimation_pipeline"
        assert result.execution_id == execution_id

    @pytest.mark.asyncio
    async def test_mermaid_contains_flowchart_keyword(self):
        """生成されたMermaidに flowchart キーワードが含まれる。"""
        execution_id = _make_execution_id()
        company_id = _make_company_id()
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.04
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            result = await generate_flow_diagram("estimation_pipeline", execution_id, mock_db)

        assert "flowchart" in result.mermaid.lower()

    @pytest.mark.asyncio
    async def test_hitl_style_in_mermaid(self):
        """MermaidにHITLの黄色スタイルが含まれる。"""
        execution_id = _make_execution_id()
        company_id = _make_company_id()
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.04
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            result = await generate_flow_diagram("estimation_pipeline", execution_id, mock_db)

        assert "FFD700" in result.mermaid

    @pytest.mark.asyncio
    async def test_without_execution_id(self):
        """execution_id なしでも動作する（定義のみ表示）。"""
        mock_db = _make_supabase_mock([])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.02
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            result = await generate_flow_diagram("estimation_pipeline", None, mock_db)

        assert isinstance(result, FlowDiagramResult)
        assert result.execution_id is None

    @pytest.mark.asyncio
    async def test_model_used_and_cost_set(self):
        """model_used と cost_yen が設定される。"""
        execution_id = _make_execution_id()
        company_id = _make_company_id()
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.04
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            result = await generate_flow_diagram("estimation_pipeline", execution_id, mock_db)

        assert result.model_used == "gemini-2.0-flash"
        assert result.cost_yen == 0.04

    @pytest.mark.asyncio
    async def test_llm_task_uses_fast_tier(self):
        """LLMタスクが FAST tier で呼ばれる。"""
        from llm.client import ModelTier
        execution_id = _make_execution_id()
        company_id = _make_company_id()
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_FLOW_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.02
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.flow_generator.get_llm_client", return_value=mock_llm
        ):
            await generate_flow_diagram("estimation_pipeline", execution_id, mock_db)

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        assert task.tier == ModelTier.FAST

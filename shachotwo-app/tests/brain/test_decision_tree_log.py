"""Tests for brain/visualization/decision_tree.py — execution_logs から判断根拠ツリーを生成する新機能のテスト。

decision_rules テーブルからのgenerate_decision_tree()テストは test_visualization.py でカバー済み。
このファイルでは generate_decision_tree_from_log + DecisionTreeResult / DecisionNode モデルをテストする。
"""
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from brain.visualization.decision_tree import (
    DecisionEdge,
    DecisionNode,
    DecisionTreeResult,
    _build_fallback_mermaid,
    _build_log_context,
    _parse_tree_result,
    generate_decision_tree_from_log,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_execution_id() -> str:
    return str(uuid4())


def _make_execution_log(execution_id: str, company_id: str) -> dict:
    return {
        "id": execution_id,
        "company_id": company_id,
        "triggered_by": "user",
        "overall_success": True,
        "created_at": "2025-01-15T10:30:00Z",
        "operations": {
            "steps": [
                {
                    "id": "S1",
                    "name": "ドキュメント受信",
                    "status": "completed",
                    "requires_human": False,
                },
                {
                    "id": "S2",
                    "name": "LLM分析",
                    "status": "completed",
                    "requires_human": False,
                },
                {
                    "id": "H1",
                    "name": "人間承認",
                    "status": "completed",
                    "requires_human": True,
                },
                {
                    "id": "S3",
                    "name": "見積書生成",
                    "status": "completed",
                    "requires_human": False,
                },
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


MOCK_TREE_RESPONSE = json.dumps({
    "mermaid": (
        "flowchart TD\n"
        '    S1["ドキュメント受信"] --> S2\n'
        '    S2["LLM分析"] --> H1\n'
        '    H1[["人間承認"]] --> S3\n'
        '    S3["見積書生成"]\n'
        "    style H1 fill:#FFD700,stroke:#B8860B"
    ),
    "nodes": [
        {"id": "S1", "label": "ドキュメント受信", "node_type": "action"},
        {"id": "S2", "label": "LLM分析", "node_type": "action"},
        {"id": "H1", "label": "人間承認", "node_type": "hitl"},
        {"id": "S3", "label": "見積書生成", "node_type": "action"},
    ],
    "edges": [
        {"from_node": "S1", "to_node": "S2", "label": ""},
        {"from_node": "S2", "to_node": "H1", "label": ""},
        {"from_node": "H1", "to_node": "S3", "label": "承認"},
    ],
})


# ---------------------------------------------------------------------------
# TestDecisionNodeModel
# ---------------------------------------------------------------------------

class TestDecisionNodeModel:

    def test_node_types(self):
        """各ノードタイプが正しく設定される。"""
        for ntype in ["condition", "action", "result", "hitl"]:
            node = DecisionNode(id="N1", label="テスト", node_type=ntype)
            assert node.node_type == ntype

    def test_label_truncated(self):
        """ラベルが設定される。"""
        node = DecisionNode(id="N1", label="テスト", node_type="action")
        assert node.label == "テスト"


# ---------------------------------------------------------------------------
# TestDecisionEdgeModel
# ---------------------------------------------------------------------------

class TestDecisionEdgeModel:

    def test_edge_fields(self):
        """エッジの from_node / to_node / label が設定される。"""
        edge = DecisionEdge(from_node="A", to_node="B", label="Yes")
        assert edge.from_node == "A"
        assert edge.to_node == "B"
        assert edge.label == "Yes"

    def test_edge_default_label(self):
        """label のデフォルトは空文字。"""
        edge = DecisionEdge(from_node="A", to_node="B")
        assert edge.label == ""


# ---------------------------------------------------------------------------
# TestParseTreeResult
# ---------------------------------------------------------------------------

class TestParseTreeResult:

    def test_parse_valid_response(self):
        """正常なLLMレスポンスをDecisionTreeResultにパースできる。"""
        result = _parse_tree_result(MOCK_TREE_RESPONSE)
        assert len(result.nodes) == 4
        assert len(result.edges) == 3
        assert "flowchart TD" in result.mermaid

    def test_parse_node_types_correct(self):
        """ノードタイプが正しくパースされる。"""
        result = _parse_tree_result(MOCK_TREE_RESPONSE)
        node_types = {n.node_type for n in result.nodes}
        assert "action" in node_types
        assert "hitl" in node_types

    def test_parse_hitl_node_present(self):
        """HITLノードが正しく識別される。"""
        result = _parse_tree_result(MOCK_TREE_RESPONSE)
        hitl_nodes = [n for n in result.nodes if n.node_type == "hitl"]
        assert len(hitl_nodes) == 1
        assert hitl_nodes[0].label == "人間承認"

    def test_parse_mermaid_contains_style(self):
        """生成されたMermaidにHITLのスタイルが含まれる。"""
        result = _parse_tree_result(MOCK_TREE_RESPONSE)
        assert "FFD700" in result.mermaid  # 黄色スタイル

    def test_parse_with_codeblock(self):
        """コードブロックに囲まれたJSONもパースできる。"""
        content = f"```json\n{MOCK_TREE_RESPONSE}\n```"
        result = _parse_tree_result(content)
        assert len(result.nodes) == 4

    def test_parse_invalid_json_returns_fallback(self):
        """無効なJSONはフォールバックMermaidを返す（クラッシュしない）。"""
        result = _parse_tree_result("これはJSONではありません")
        assert isinstance(result, DecisionTreeResult)
        assert "flowchart" in result.mermaid.lower()


# ---------------------------------------------------------------------------
# TestBuildLogContext
# ---------------------------------------------------------------------------

class TestBuildLogContext:

    def test_context_contains_execution_id(self):
        """コンテキストに実行IDが含まれる。"""
        execution_id = _make_execution_id()
        log = {
            "id": execution_id,
            "triggered_by": "user",
            "overall_success": True,
            "created_at": "2025-01-01T00:00:00Z",
        }
        context = _build_log_context(log, {})
        assert execution_id[:8] in context

    def test_context_contains_operations(self):
        """operationsがある場合、コンテキストに含まれる。"""
        log = {
            "id": str(uuid4()),
            "triggered_by": "schedule",
            "overall_success": False,
            "created_at": "2025-01-01T00:00:00Z",
        }
        operations = {"steps": [{"id": "S1", "name": "テストステップ", "status": "failed"}]}
        context = _build_log_context(log, operations)
        assert "テストステップ" in context


# ---------------------------------------------------------------------------
# TestBuildFallbackMermaid
# ---------------------------------------------------------------------------

class TestBuildFallbackMermaid:

    def test_fallback_includes_all_nodes(self):
        """フォールバックMermaidに全ノードが含まれる。"""
        nodes = [
            DecisionNode(id="A", label="開始", node_type="start"),
            DecisionNode(id="H1", label="承認", node_type="hitl"),
            DecisionNode(id="B", label="終了", node_type="end"),
        ]
        edges = [
            DecisionEdge(from_node="A", to_node="H1"),
            DecisionEdge(from_node="H1", to_node="B", label="承認済"),
        ]
        mermaid = _build_fallback_mermaid(nodes, edges)
        assert "開始" in mermaid
        assert "承認" in mermaid
        assert "終了" in mermaid

    def test_fallback_hitl_nodes_have_yellow_style(self):
        """HITLノードに黄色スタイルが適用される。"""
        nodes = [
            DecisionNode(id="H1", label="人間承認", node_type="hitl"),
        ]
        mermaid = _build_fallback_mermaid(nodes, [])
        assert "FFD700" in mermaid

    def test_fallback_edge_labels_included(self):
        """エッジラベルがMermaidに含まれる。"""
        nodes = [
            DecisionNode(id="A", label="判断", node_type="condition"),
            DecisionNode(id="B", label="処理", node_type="action"),
        ]
        edges = [DecisionEdge(from_node="A", to_node="B", label="Yes")]
        mermaid = _build_fallback_mermaid(nodes, edges)
        assert "Yes" in mermaid


# ---------------------------------------------------------------------------
# TestGenerateDecisionTreeFromLog
# ---------------------------------------------------------------------------

class TestGenerateDecisionTreeFromLog:

    @pytest.mark.asyncio
    async def test_returns_tree_result(self):
        """generate_decision_tree_from_log が DecisionTreeResult を返す。"""
        execution_id = _make_execution_id()
        company_id = str(uuid4())
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_TREE_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.03
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.decision_tree.get_llm_client", return_value=mock_llm
        ):
            result = await generate_decision_tree_from_log(execution_id, mock_db)

        assert isinstance(result, DecisionTreeResult)
        assert result.source == "execution_log"
        assert result.execution_id == execution_id

    @pytest.mark.asyncio
    async def test_execution_not_found_returns_fallback(self):
        """実行ログが存在しない場合、フォールバックMermaidを返す。"""
        execution_id = _make_execution_id()
        mock_db = _make_supabase_mock([])

        result = await generate_decision_tree_from_log(execution_id, mock_db)

        assert isinstance(result, DecisionTreeResult)
        assert result.execution_id == execution_id
        assert "見つかりません" in result.mermaid

    @pytest.mark.asyncio
    async def test_mermaid_contains_flowchart(self):
        """生成されたMermaidに flowchart キーワードが含まれる。"""
        execution_id = _make_execution_id()
        company_id = str(uuid4())
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_TREE_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.03
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.decision_tree.get_llm_client", return_value=mock_llm
        ):
            result = await generate_decision_tree_from_log(execution_id, mock_db)

        assert "flowchart" in result.mermaid.lower()

    @pytest.mark.asyncio
    async def test_hitl_node_visible_in_result(self):
        """HITLノードが結果に含まれる。"""
        execution_id = _make_execution_id()
        company_id = str(uuid4())
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_TREE_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.03
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.decision_tree.get_llm_client", return_value=mock_llm
        ):
            result = await generate_decision_tree_from_log(execution_id, mock_db)

        hitl_nodes = [n for n in result.nodes if n.node_type == "hitl"]
        assert len(hitl_nodes) >= 1

    @pytest.mark.asyncio
    async def test_model_used_and_cost_set(self):
        """model_used と cost_yen が設定される。"""
        execution_id = _make_execution_id()
        company_id = str(uuid4())
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_TREE_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.03
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.decision_tree.get_llm_client", return_value=mock_llm
        ):
            result = await generate_decision_tree_from_log(execution_id, mock_db)

        assert result.model_used == "gemini-2.0-flash"
        assert result.cost_yen == 0.03

    @pytest.mark.asyncio
    async def test_llm_task_uses_fast_tier(self):
        """LLMタスクが FAST tier で呼ばれる。"""
        from llm.client import ModelTier
        execution_id = _make_execution_id()
        company_id = str(uuid4())
        log = _make_execution_log(execution_id, company_id)
        mock_db = _make_supabase_mock([log])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_TREE_RESPONSE
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.02
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.visualization.decision_tree.get_llm_client", return_value=mock_llm
        ):
            await generate_decision_tree_from_log(execution_id, mock_db)

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        assert task.tier == ModelTier.FAST

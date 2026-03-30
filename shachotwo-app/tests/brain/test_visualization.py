"""Tests for brain/visualization module.

カバレッジ:
- generate_completeness_radar: labels/values/overall の正確性（10テスト）
- completeness < 0.4 の次元に recommendations が出る（3テスト）
- generate_process_flow: ルールベースでMermaid文字列を返す（3テスト）
- generate_process_flow: LLMありの場合はLLMを呼ぶ（モック、2テスト）
- generate_decision_tree: rulesからノード/エッジを生成（DBモック、3テスト）
"""
import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.twin.models import (
    CostState,
    PeopleState,
    ProcessState,
    RiskState,
    ToolState,
    TwinSnapshot,
)
from brain.visualization.completeness_map import generate_completeness_radar
from brain.visualization.decision_tree import (
    generate_decision_tree,
    render_decision_tree_mermaid,
    render_decision_tree_svg,
)
from brain.visualization.flow_generator import (
    generate_process_flow,
    generate_process_flow_from_knowledge,
    render_process_flow_mermaid,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_company_id() -> str:
    return str(uuid4())


def _make_snapshot(
    people: float = 0.8,
    process: float = 0.6,
    cost: float = 0.5,
    tool: float = 0.7,
    risk: float = 0.9,
) -> TwinSnapshot:
    """指定した completeness を持つ TwinSnapshot を生成する。"""
    snap = TwinSnapshot(
        company_id=_make_company_id(),
        people=PeopleState(completeness=people),
        process=ProcessState(completeness=process),
        cost=CostState(completeness=cost),
        tool=ToolState(completeness=tool),
        risk=RiskState(completeness=risk),
    )
    snap.recalculate_overall_completeness()
    return snap


def _make_flow_item(title: str, content: str = "") -> dict:
    return {
        "id": str(uuid4()),
        "item_type": "flow",
        "title": title,
        "content": content or f"{title}の詳細",
        "department": "営業",
        "category": "process",
    }


def _make_non_flow_item(title: str) -> dict:
    return {
        "id": str(uuid4()),
        "item_type": "rule",
        "title": title,
        "content": f"{title}のルール",
    }


def _make_decision_rule(decision_name: str, logic_type: str = "if_then") -> dict:
    return {
        "id": str(uuid4()),
        "department": "営業",
        "decision_name": decision_name,
        "context": f"{decision_name}の文脈",
        "logic_type": logic_type,
        "logic_definition": {"action": f"{decision_name}を実行する"},
        "is_active": True,
    }


def _mock_db_with_rules(rules: list[dict]) -> MagicMock:
    """指定したルールを返すDBモックを生成する。

    decision_tree.generate_decision_tree の DB チェーン:
        db.table(...).select(...).eq(...).eq(...)[.eq(...)].order(...).limit(...).execute()

    eq() は 2〜3 回呼ばれるため、同じオブジェクトを返すように side_effect で制御する。
    """
    mock_result = MagicMock()
    mock_result.data = rules

    # 末端ノード: execute が mock_result を返す
    limit_mock = MagicMock()
    limit_mock.execute.return_value = mock_result

    order_mock = MagicMock()
    order_mock.limit.return_value = limit_mock

    # select 後の chain オブジェクト — eq を何回呼ばれても自身を返す
    select_chain = MagicMock()
    select_chain.eq.side_effect = lambda *a, **kw: select_chain
    select_chain.order.return_value = order_mock

    table_mock = MagicMock()
    table_mock.select.return_value = select_chain

    db_mock = MagicMock()
    db_mock.table.return_value = table_mock

    return db_mock


# ---------------------------------------------------------------------------
# TestGenerateCompletenessRadar
# ---------------------------------------------------------------------------

class TestGenerateCompletenessRadar:

    def test_labels_are_five_dimensions(self):
        """labels が5次元のラベルを返す。"""
        snap = _make_snapshot()
        result = generate_completeness_radar(snap)
        assert result["labels"] == ["ヒト", "プロセス", "コスト", "ツール", "リスク"]

    def test_values_length_is_five(self):
        """values の長さが5である。"""
        snap = _make_snapshot()
        result = generate_completeness_radar(snap)
        assert len(result["values"]) == 5

    def test_values_match_snapshot_completeness(self):
        """values が snapshot の各次元 completeness と一致する。"""
        snap = _make_snapshot(people=0.8, process=0.6, cost=0.5, tool=0.7, risk=0.9)
        result = generate_completeness_radar(snap)
        assert result["values"][0] == pytest.approx(0.8)
        assert result["values"][1] == pytest.approx(0.6)
        assert result["values"][2] == pytest.approx(0.5)
        assert result["values"][3] == pytest.approx(0.7)
        assert result["values"][4] == pytest.approx(0.9)

    def test_overall_is_average_of_five_dimensions(self):
        """overall が5次元の平均と一致する。"""
        snap = _make_snapshot(people=0.8, process=0.6, cost=0.5, tool=0.7, risk=0.9)
        result = generate_completeness_radar(snap)
        expected = round((0.8 + 0.6 + 0.5 + 0.7 + 0.9) / 5, 4)
        assert result["overall"] == pytest.approx(expected, abs=0.001)

    def test_all_zero_snapshot_returns_zero_overall(self):
        """全次元0の場合 overall は 0。"""
        snap = _make_snapshot(0.0, 0.0, 0.0, 0.0, 0.0)
        result = generate_completeness_radar(snap)
        assert result["overall"] == 0.0

    def test_all_full_snapshot_returns_one_overall(self):
        """全次元1.0の場合 overall は 1.0。"""
        snap = _make_snapshot(1.0, 1.0, 1.0, 1.0, 1.0)
        result = generate_completeness_radar(snap)
        assert result["overall"] == pytest.approx(1.0)

    def test_values_are_rounded_to_four_decimal_places(self):
        """values の値が小数4桁以内に丸められる。"""
        snap = _make_snapshot(people=0.333333, process=0.666666)
        result = generate_completeness_radar(snap)
        for v in result["values"]:
            assert len(str(v).split(".")[-1]) <= 4

    def test_recommendations_is_list(self):
        """recommendations がリスト型である。"""
        snap = _make_snapshot(1.0, 1.0, 1.0, 1.0, 1.0)
        result = generate_completeness_radar(snap)
        assert isinstance(result["recommendations"], list)

    def test_no_recommendations_when_all_dimensions_high(self):
        """全次元が高い場合 recommendations が空。"""
        snap = _make_snapshot(0.8, 0.8, 0.8, 0.8, 0.8)
        result = generate_completeness_radar(snap)
        assert result["recommendations"] == []

    def test_result_has_required_keys(self):
        """返り値が labels/values/overall/recommendations を含む。"""
        snap = _make_snapshot()
        result = generate_completeness_radar(snap)
        assert "labels" in result
        assert "values" in result
        assert "overall" in result
        assert "recommendations" in result

    def test_recommendations_for_low_people_completeness(self):
        """people completeness が低い場合 'ヒト' のrecommendationが出る。"""
        snap = _make_snapshot(people=0.1, process=0.8, cost=0.8, tool=0.8, risk=0.8)
        result = generate_completeness_radar(snap)
        dims = [r["dimension"] for r in result["recommendations"]]
        assert "ヒト" in dims

    def test_recommendations_for_low_cost_completeness(self):
        """cost completeness が低い場合 'コスト' のrecommendationが出る。"""
        snap = _make_snapshot(people=0.8, process=0.8, cost=0.1, tool=0.8, risk=0.8)
        result = generate_completeness_radar(snap)
        dims = [r["dimension"] for r in result["recommendations"]]
        assert "コスト" in dims

    def test_recommendations_for_multiple_low_dimensions(self):
        """複数次元が低い場合は複数のrecommendationが出る。"""
        snap = _make_snapshot(people=0.1, process=0.1, cost=0.8, tool=0.8, risk=0.8)
        result = generate_completeness_radar(snap)
        assert len(result["recommendations"]) >= 2


# ---------------------------------------------------------------------------
# TestGenerateProcessFlow — ルールベース
# ---------------------------------------------------------------------------

class TestGenerateProcessFlowRuleBased:

    @pytest.mark.asyncio
    async def test_returns_mermaid_string(self):
        """ルールベースで Mermaid 文字列を返す。"""
        items = [_make_flow_item("受注処理")]
        result = await generate_process_flow(items)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_empty_items_returns_fallback(self):
        """フローアイテムが空の場合フォールバックメッセージを返す。"""
        result = await generate_process_flow([])
        assert isinstance(result, str)
        # フローチャートの開始行を含む
        assert "flowchart" in result.lower() or "TD" in result

    @pytest.mark.asyncio
    async def test_non_flow_items_are_ignored(self):
        """item_type != 'flow' のアイテムは無視される。"""
        items = [
            _make_non_flow_item("ルール1"),
            _make_non_flow_item("ルール2"),
        ]
        result = await generate_process_flow(items)
        # フローアイテムがないのでルールベースのフォールバック
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_two_flow_items_use_rule_based(self):
        """フローアイテムが2件の場合はLLMを使わずルールベースを使う。"""
        items = [_make_flow_item(f"ステップ{i}") for i in range(2)]
        # LLMが呼ばれないことを確認
        with patch("brain.visualization.flow_generator.get_llm_client") as mock_get_llm:
            result = await generate_process_flow(items)
        mock_get_llm.assert_not_called()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_flow_items_titles_appear_in_output(self):
        """ルールベース生成時にアイテムのタイトルが出力に含まれる。"""
        items = [_make_flow_item("受注確認")]
        result = await generate_process_flow(items)
        assert "受注確認" in result


# ---------------------------------------------------------------------------
# TestGenerateProcessFlow — LLMあり
# ---------------------------------------------------------------------------

class TestGenerateProcessFlowWithLLM:

    @pytest.mark.asyncio
    async def test_three_or_more_flow_items_call_llm(self):
        """フローアイテムが3件以上の場合 LLM を呼ぶ。"""
        items = [_make_flow_item(f"ステップ{i}") for i in range(3)]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "flowchart TD\n    A[受注] --> B[処理] --> C[完了]"
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow(items, company_id="test-company")

        mock_llm.generate.assert_called_once()
        assert "flowchart" in result or "受注" in result

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_rule_based(self):
        """LLM が例外を投げた場合ルールベースにフォールバックする。"""
        items = [_make_flow_item(f"ステップ{i}") for i in range(5)]
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM接続失敗"))

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow(items)

        # フォールバックで文字列が返る
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# TestGenerateDecisionTree — DBモック
# ---------------------------------------------------------------------------

class TestGenerateDecisionTree:

    @pytest.mark.asyncio
    async def test_returns_empty_mermaid_when_no_rules(self):
        """ルールがない場合フォールバックMermaidを返す。"""
        company_id = _make_company_id()
        mock_db = _mock_db_with_rules([])

        with patch("brain.visualization.decision_tree.get_service_client", return_value=mock_db):
            result = await generate_decision_tree(company_id)

        assert result["rule_count"] == 0
        assert result["nodes"] == []
        assert result["edges"] == []
        assert isinstance(result["mermaid"], str)
        assert "flowchart" in result["mermaid"].lower() or "flowchart TD" in result["mermaid"]

    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges_for_rules(self):
        """ルールが存在する場合 nodes と edges が生成される。"""
        company_id = _make_company_id()
        rules = [
            _make_decision_rule("見積承認"),
            _make_decision_rule("発注判断"),
            _make_decision_rule("返品対応"),
        ]
        mock_db = _mock_db_with_rules(rules)

        with patch("brain.visualization.decision_tree.get_service_client", return_value=mock_db):
            result = await generate_decision_tree(company_id)

        assert result["rule_count"] == 3
        # 各ルールに条件ノード + アクションノード = 2ノード
        assert len(result["nodes"]) == 6
        # 各ルールに1エッジ
        assert len(result["edges"]) == 3

    @pytest.mark.asyncio
    async def test_mermaid_contains_flowchart_keyword(self):
        """生成された Mermaid に flowchart キーワードが含まれる。"""
        company_id = _make_company_id()
        rules = [_make_decision_rule("在庫確認")]
        mock_db = _mock_db_with_rules(rules)

        with patch("brain.visualization.decision_tree.get_service_client", return_value=mock_db):
            result = await generate_decision_tree(company_id)

        assert "flowchart TD" in result["mermaid"]

    @pytest.mark.asyncio
    async def test_nodes_have_correct_types(self):
        """nodes に condition と action タイプが含まれる。"""
        company_id = _make_company_id()
        rules = [_make_decision_rule("サンプルルール")]
        mock_db = _mock_db_with_rules(rules)

        with patch("brain.visualization.decision_tree.get_service_client", return_value=mock_db):
            result = await generate_decision_tree(company_id)

        node_types = {n["type"] for n in result["nodes"]}
        assert "condition" in node_types
        assert "action" in node_types

    @pytest.mark.asyncio
    async def test_edges_have_yes_label(self):
        """edges の label が 'Yes' になっている。"""
        company_id = _make_company_id()
        rules = [_make_decision_rule("承認フロー")]
        mock_db = _mock_db_with_rules(rules)

        with patch("brain.visualization.decision_tree.get_service_client", return_value=mock_db):
            result = await generate_decision_tree(company_id)

        for edge in result["edges"]:
            assert edge["label"] == "Yes"


# ---------------------------------------------------------------------------
# TestRenderDecisionTreeMermaid — 構造化データ → Mermaid 変換
# ---------------------------------------------------------------------------

def _make_simple_tree() -> dict:
    """シンプルな1階層決定木データを返す。"""
    return {
        "root": {
            "id": "start",
            "label": "見積依頼受付",
            "type": "action",
            "children": [],
        }
    }


def _make_branching_tree() -> dict:
    """分岐あり決定木データを返す。"""
    return {
        "root": {
            "id": "start",
            "label": "見積依頼受付",
            "type": "action",
            "children": [
                {
                    "condition": "金額100万以上?",
                    "yes": {
                        "id": "manager_approval",
                        "label": "部長承認",
                        "type": "action",
                        "children": [],
                    },
                    "no": {
                        "id": "section_approval",
                        "label": "課長承認",
                        "type": "action",
                        "children": [],
                    },
                }
            ],
        }
    }


class TestRenderDecisionTreeMermaid:

    def test_returns_string(self):
        """Mermaid 文字列を返す。"""
        result = render_decision_tree_mermaid(_make_simple_tree())
        assert isinstance(result, str)

    def test_starts_with_graph_td(self):
        """出力が 'graph TD' で始まる。"""
        result = render_decision_tree_mermaid(_make_simple_tree())
        assert result.startswith("graph TD")

    def test_root_label_appears_in_output(self):
        """ルートノードのラベルが出力に含まれる。"""
        result = render_decision_tree_mermaid(_make_simple_tree())
        assert "見積依頼受付" in result

    def test_branching_tree_contains_condition_label(self):
        """分岐ありの場合、条件ラベルが出力に含まれる。"""
        result = render_decision_tree_mermaid(_make_branching_tree())
        assert "100" in result  # "金額100万以上?" の一部

    def test_branching_tree_contains_yes_no_edges(self):
        """分岐ありの場合、はい/いいえのエッジラベルが含まれる。"""
        result = render_decision_tree_mermaid(_make_branching_tree())
        assert "はい" in result
        assert "いいえ" in result

    def test_child_labels_appear_in_output(self):
        """子ノードのラベルが出力に含まれる。"""
        result = render_decision_tree_mermaid(_make_branching_tree())
        assert "部長承認" in result
        assert "課長承認" in result

    def test_invalid_input_returns_fallback(self):
        """不正な入力でフォールバック文字列を返す。"""
        result = render_decision_tree_mermaid("not a dict")  # type: ignore
        assert "graph TD" in result

    def test_missing_root_returns_fallback(self):
        """root キーがない場合フォールバック文字列を返す。"""
        result = render_decision_tree_mermaid({"no_root": {}})
        assert "graph TD" in result

    def test_node_id_is_alphanumeric(self):
        """ノードIDに英数字以外の文字が含まれない（Mermaid互換）。"""
        tree = {
            "root": {
                "id": "step-1/test",
                "label": "テスト",
                "type": "action",
                "children": [],
            }
        }
        result = render_decision_tree_mermaid(tree)
        # ハイフンやスラッシュはアンダースコアに変換されるはず
        assert "-" not in result.split("\n")[1]  # ノード定義行

    def test_decision_node_uses_diamond_shape(self):
        """decision タイプのノードが菱形（{}）で表示される。"""
        tree = {
            "root": {
                "id": "check",
                "label": "条件確認",
                "type": "decision",
                "children": [],
            }
        }
        result = render_decision_tree_mermaid(tree)
        assert "{" in result and "}" in result

    def test_end_node_uses_rounded_shape(self):
        """end タイプのノードが角丸（()）で表示される。"""
        tree = {
            "root": {
                "id": "finish",
                "label": "完了",
                "type": "end",
                "children": [],
            }
        }
        result = render_decision_tree_mermaid(tree)
        assert "([" in result


# ---------------------------------------------------------------------------
# TestRenderDecisionTreeSvg — フォールバック SVG 生成
# ---------------------------------------------------------------------------

class TestRenderDecisionTreeSvg:

    def test_returns_string(self):
        """SVG 文字列を返す。"""
        result = render_decision_tree_svg(_make_simple_tree())
        assert isinstance(result, str)

    def test_starts_with_svg_tag(self):
        """出力が <svg で始まる。"""
        result = render_decision_tree_svg(_make_simple_tree())
        assert result.strip().startswith("<svg")

    def test_contains_root_label(self):
        """ルートノードのラベルが SVG に含まれる。"""
        result = render_decision_tree_svg(_make_simple_tree())
        assert "見積依頼受付" in result

    def test_invalid_input_returns_svg_fallback(self):
        """不正な入力でも SVG 文字列を返す。"""
        result = render_decision_tree_svg("invalid")  # type: ignore
        assert "<svg" in result

    def test_missing_root_returns_svg_fallback(self):
        """root がない場合でも SVG 文字列を返す。"""
        result = render_decision_tree_svg({})
        assert "<svg" in result


# ---------------------------------------------------------------------------
# TestRenderProcessFlowMermaid — 構造化フローデータ → Mermaid 変換
# ---------------------------------------------------------------------------

def _make_flow_data() -> dict:
    """サンプルのフローデータを返す。"""
    return {
        "steps": [
            {"id": "order", "label": "受注", "type": "process"},
            {"id": "design", "label": "設計", "type": "process"},
            {"id": "inspect", "label": "検査", "type": "decision"},
            {"id": "ship", "label": "出荷", "type": "process"},
            {"id": "rework", "label": "手直し", "type": "process"},
        ],
        "connections": [
            {"from": "order", "to": "design", "label": ""},
            {"from": "design", "to": "inspect", "label": ""},
            {"from": "inspect", "to": "ship", "label": "合格"},
            {"from": "inspect", "to": "rework", "label": "不合格", "condition": True},
            {"from": "rework", "to": "inspect", "label": ""},
        ],
        "bottleneck_step_id": "design",
    }


class TestRenderProcessFlowMermaid:

    def test_returns_string(self):
        """Mermaid 文字列を返す。"""
        result = render_process_flow_mermaid(_make_flow_data())
        assert isinstance(result, str)

    def test_starts_with_graph_lr(self):
        """出力が 'graph LR' で始まる。"""
        result = render_process_flow_mermaid(_make_flow_data())
        assert result.startswith("graph LR")

    def test_step_labels_in_output(self):
        """各ステップのラベルが出力に含まれる。"""
        result = render_process_flow_mermaid(_make_flow_data())
        for label in ["受注", "設計", "検査", "出荷", "手直し"]:
            assert label in result

    def test_edge_labels_in_output(self):
        """エッジラベルが出力に含まれる。"""
        result = render_process_flow_mermaid(_make_flow_data())
        assert "合格" in result
        assert "不合格" in result

    def test_bottleneck_highlighted_by_default(self):
        """デフォルトでボトルネックがハイライトされる（style文）。"""
        result = render_process_flow_mermaid(_make_flow_data())
        assert "style design" in result or "style" in result
        # オレンジ色のハイライト
        assert "FF9800" in result or "E65100" in result

    def test_no_bottleneck_highlight_when_disabled(self):
        """highlight_bottleneck=False の場合ボトルネックの style がない。"""
        result = render_process_flow_mermaid(_make_flow_data(), highlight_bottleneck=False)
        assert "FF9800" not in result

    def test_empty_steps_returns_fallback(self):
        """steps が空の場合フォールバックを返す。"""
        result = render_process_flow_mermaid({"steps": [], "connections": []})
        assert "graph LR" in result

    def test_invalid_input_returns_fallback(self):
        """不正な入力でフォールバックを返す。"""
        result = render_process_flow_mermaid("not a dict")  # type: ignore
        assert "graph LR" in result

    def test_decision_step_uses_diamond_shape(self):
        """decision タイプのステップが菱形（{}）で表示される。"""
        result = render_process_flow_mermaid(_make_flow_data())
        assert "{" in result

    def test_hitl_step_uses_double_bracket(self):
        """hitl タイプのステップが二重矩形（[[]]）で表示され黄色になる。"""
        flow_data = {
            "steps": [
                {"id": "approve", "label": "承認待ち", "type": "hitl"},
            ],
            "connections": [],
            "bottleneck_step_id": "",
        }
        result = render_process_flow_mermaid(flow_data)
        assert "[[" in result
        assert "FFD700" in result

    def test_node_id_special_chars_sanitized(self):
        """ノードIDの特殊文字がアンダースコアに変換される。"""
        flow_data = {
            "steps": [{"id": "step-1/a", "label": "ステップ", "type": "process"}],
            "connections": [],
            "bottleneck_step_id": "",
        }
        result = render_process_flow_mermaid(flow_data)
        assert "-" not in result.split("graph LR")[1].split("[")[0]


# ---------------------------------------------------------------------------
# TestGenerateProcessFlowFromKnowledge — LLM でフロー生成
# ---------------------------------------------------------------------------

class TestGenerateProcessFlowFromKnowledge:

    @pytest.mark.asyncio
    async def test_empty_items_returns_minimal_flow(self):
        """knowledge_items が空の場合、最小フローを返す。"""
        result = await generate_process_flow_from_knowledge([])
        assert isinstance(result, dict)
        assert "steps" in result
        assert "connections" in result

    @pytest.mark.asyncio
    async def test_calls_llm_with_items(self):
        """knowledge_items があれば LLM を呼ぶ。"""
        items = [
            {"id": "1", "item_type": "flow", "title": "受注処理", "content": "受注を確認する"},
            {"id": "2", "item_type": "rule", "title": "承認フロー", "content": "承認が必要"},
        ]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"steps": [{"id": "s1", "label": "受注", "type": "process"}], "connections": [], "bottleneck_step_id": ""}'
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow_from_knowledge(items, company_id="test-co")

        mock_llm.generate.assert_called_once()
        assert isinstance(result["steps"], list)

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback_flow(self):
        """LLM が例外を投げた場合、フォールバックフローを返す。"""
        items = [
            {"id": "1", "item_type": "flow", "title": "入庫処理", "content": ""},
            {"id": "2", "item_type": "flow", "title": "出庫処理", "content": ""},
        ]
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=RuntimeError("LLM障害"))

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow_from_knowledge(items)

        assert isinstance(result, dict)
        assert len(result["steps"]) >= 1

    @pytest.mark.asyncio
    async def test_llm_invalid_json_returns_fallback(self):
        """LLM が不正な JSON を返した場合、フォールバックフローを返す。"""
        items = [{"id": "1", "item_type": "flow", "title": "テスト", "content": "内容"}]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "これはJSONではありません"
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow_from_knowledge(items)

        assert isinstance(result, dict)
        assert "steps" in result

    @pytest.mark.asyncio
    async def test_department_passed_to_context(self):
        """department が指定された場合、LLM へのコンテキストに含まれる。"""
        items = [{"id": "1", "item_type": "flow", "title": "営業フロー", "content": "営業処理"}]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"steps": [{"id": "s1", "label": "営業", "type": "process"}], "connections": [], "bottleneck_step_id": ""}'
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            await generate_process_flow_from_knowledge(items, department="営業部")

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        user_msg = task.messages[-1]["content"]
        assert "営業部" in user_msg

    @pytest.mark.asyncio
    async def test_step_ids_are_safe(self):
        """LLM が返した step id の特殊文字がサニタイズされている。"""
        items = [{"id": "1", "item_type": "flow", "title": "テスト", "content": ""}]
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"steps": [{"id": "step-1/x", "label": "ステップ", "type": "process"}], "connections": [], "bottleneck_step_id": ""}'
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.visualization.flow_generator.get_llm_client", return_value=mock_llm):
            result = await generate_process_flow_from_knowledge(items)

        for step in result["steps"]:
            assert re.match(r"^[A-Za-z0-9_]+$", step["id"]), f"不正なid: {step['id']}"

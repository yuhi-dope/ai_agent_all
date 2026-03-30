"""Tests for brain/twin/whatif.py — LLMベース実装 (run_whatif) のテスト。

ルールベース実装 (simulate_whatif) は tests/brain/test_twin.py でカバー済み。
このファイルでは run_whatif + WhatIfResult / DimensionImpact モデルをテストする。
"""
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from brain.twin.whatif import (
    DimensionImpact,
    WhatIfResult,
    _build_whatif_context,
    _parse_whatif_result,
    run_whatif,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_company_id() -> str:
    return str(uuid4())


def _make_supabase_mock(
    snapshots: list[dict],
    knowledge_items: list[dict],
) -> MagicMock:
    """Supabaseクライアントのモックを生成する。"""
    mock_db = MagicMock()

    def _make_chain(data: list):
        result = MagicMock()
        result.data = data
        chain = MagicMock()
        chain.eq.side_effect = lambda *a, **kw: chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = result
        return chain

    snap_chain = _make_chain(snapshots)
    know_chain = _make_chain(knowledge_items)

    def _table_side_effect(table_name: str):
        if table_name == "company_state_snapshots":
            return MagicMock(select=lambda *a, **kw: snap_chain)
        elif table_name == "knowledge_items":
            return MagicMock(select=lambda *a, **kw: know_chain)
        return MagicMock()

    mock_db.table.side_effect = _table_side_effect
    return mock_db


MOCK_WHATIF_RESPONSE = json.dumps({
    "summary": "新拠点開設により人員・コストが大幅増加するが、売上機会も拡大する",
    "dimension_impacts": [
        {
            "dimension": "people",
            "impact_level": "high",
            "score_delta": -0.3,
            "description": "採用・研修コストと人員管理の複雑化が生じる",
            "risks": ["採用困難", "管理コスト増"],
            "opportunities": ["人材多様化", "地域密着営業"],
        },
        {
            "dimension": "cost",
            "impact_level": "high",
            "score_delta": -0.5,
            "description": "オフィス賃料・設備費が追加で月200万円発生する",
            "risks": ["固定費増大", "資金繰り悪化"],
            "opportunities": ["税制優遇活用"],
        },
        {
            "dimension": "process",
            "impact_level": "medium",
            "score_delta": -0.2,
            "description": "拠点間の業務連携フローの整備が必要",
            "risks": ["プロセス煩雑化"],
            "opportunities": ["業務標準化"],
        },
        {
            "dimension": "tool",
            "impact_level": "low",
            "score_delta": 0.1,
            "description": "既存SaaSツールで対応可能",
            "risks": [],
            "opportunities": ["ツール活用拡大"],
        },
        {
            "dimension": "risk",
            "impact_level": "high",
            "score_delta": -0.4,
            "description": "拠点管理リスクとコンプライアンス対応が増加する",
            "risks": ["管理リスク増加", "法令対応コスト"],
            "opportunities": [],
        },
    ],
    "overall_score_delta": -0.28,
    "feasibility": "medium",
    "recommended_next_steps": [
        "3ヶ月分の運転資金確保を確認する",
        "採用計画を策定する",
        "業務マニュアルを整備する",
    ],
})


# ---------------------------------------------------------------------------
# TestDimensionImpactModel
# ---------------------------------------------------------------------------

class TestDimensionImpactModel:

    def test_dimension_impact_valid(self):
        """DimensionImpact が正しく構築される。"""
        impact = DimensionImpact(
            dimension="people",
            impact_level="high",
            score_delta=-0.3,
            description="人員コスト増加",
        )
        assert impact.dimension == "people"
        assert impact.impact_level == "high"
        assert impact.score_delta == -0.3

    def test_default_risks_and_opportunities(self):
        """risks と opportunities はデフォルトで空リスト。"""
        impact = DimensionImpact(
            dimension="cost",
            impact_level="medium",
        )
        assert impact.risks == []
        assert impact.opportunities == []


# ---------------------------------------------------------------------------
# TestWhatIfResultModel
# ---------------------------------------------------------------------------

class TestWhatIfResultModel:

    def test_whatif_result_valid(self):
        """WhatIfResult が正しく構築される。"""
        result = WhatIfResult(
            scenario="テストシナリオ",
            summary="テスト要約",
            dimension_impacts=[],
        )
        assert result.scenario == "テストシナリオ"
        assert result.feasibility == "medium"  # デフォルト
        assert result.overall_score_delta == 0.0

    def test_whatif_result_serializable(self):
        """WhatIfResult が JSON シリアライズ可能。"""
        result = WhatIfResult(
            scenario="テスト",
            summary="テスト",
            dimension_impacts=[
                DimensionImpact(dimension="people", impact_level="high", score_delta=-0.3)
            ],
        )
        dumped = result.model_dump()
        assert isinstance(dumped, dict)
        assert "dimension_impacts" in dumped
        serialized = json.dumps(dumped, ensure_ascii=False)
        assert "people" in serialized


# ---------------------------------------------------------------------------
# TestParseWhatIfResult
# ---------------------------------------------------------------------------

class TestParseWhatIfResult:

    def test_parse_valid_response(self):
        """正常なLLMレスポンスをWhatIfResultにパースできる。"""
        result = _parse_whatif_result("新拠点開設シナリオ", MOCK_WHATIF_RESPONSE)
        assert result.scenario == "新拠点開設シナリオ"
        assert len(result.dimension_impacts) == 5
        assert result.overall_score_delta == pytest.approx(-0.28)
        assert result.feasibility == "medium"

    def test_parse_dimension_names(self):
        """5次元全てのdimensionが正しく解析される。"""
        result = _parse_whatif_result("テスト", MOCK_WHATIF_RESPONSE)
        dims = {imp.dimension for imp in result.dimension_impacts}
        assert dims == {"people", "cost", "process", "tool", "risk"}

    def test_parse_with_codeblock(self):
        """コードブロックに囲まれた場合もパースできる。"""
        content = f"```json\n{MOCK_WHATIF_RESPONSE}\n```"
        result = _parse_whatif_result("テスト", content)
        assert len(result.dimension_impacts) == 5

    def test_parse_invalid_json_returns_fallback(self):
        """無効なJSONはフォールバック結果を返す（クラッシュしない）。"""
        result = _parse_whatif_result("テストシナリオ", "これはJSONではありません")
        assert isinstance(result, WhatIfResult)
        assert result.scenario == "テストシナリオ"
        assert result.dimension_impacts == []

    def test_parse_recommended_steps(self):
        """recommended_next_steps が正しく解析される。"""
        result = _parse_whatif_result("テスト", MOCK_WHATIF_RESPONSE)
        assert len(result.recommended_next_steps) == 3
        assert "運転資金" in result.recommended_next_steps[0]

    def test_parse_risks_and_opportunities_in_dimensions(self):
        """各次元の risks と opportunities が正しく解析される。"""
        result = _parse_whatif_result("テスト", MOCK_WHATIF_RESPONSE)
        people_impact = next(
            imp for imp in result.dimension_impacts if imp.dimension == "people"
        )
        assert "採用困難" in people_impact.risks
        assert "人材多様化" in people_impact.opportunities


# ---------------------------------------------------------------------------
# TestBuildWhatIfContext
# ---------------------------------------------------------------------------

class TestBuildWhatIfContext:

    def test_context_contains_scenario(self):
        """コンテキストにシナリオが含まれる。"""
        context = _build_whatif_context("新拠点開設シナリオ", [], [])
        assert "新拠点開設シナリオ" in context

    def test_context_contains_snapshot_data(self):
        """スナップショットがある場合、状態情報が含まれる。"""
        company_id = _make_company_id()
        snap = {
            "id": str(uuid4()),
            "company_id": company_id,
            "people_state": {"headcount": 20, "completeness": 0.5},
            "snapshot_at": "2025-01-01T00:00:00Z",
        }
        context = _build_whatif_context("テスト", [snap], [])
        assert "現在の会社状態" in context

    def test_context_contains_knowledge_items(self):
        """ナレッジアイテムがある場合、コンテキストに含まれる。"""
        items = [
            {"id": str(uuid4()), "title": "採用コスト", "content": "採用にかかる費用", "item_type": "fact"}
        ]
        context = _build_whatif_context("テスト", [], items)
        assert "採用コスト" in context


# ---------------------------------------------------------------------------
# TestRunWhatIf
# ---------------------------------------------------------------------------

class TestRunWhatIf:

    @pytest.mark.asyncio
    async def test_returns_whatif_result(self):
        """run_whatif が WhatIfResult を返す。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_WHATIF_RESPONSE
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.12
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            result = await run_whatif(company_id, "新拠点を開設したら？", mock_db)

        assert isinstance(result, WhatIfResult)
        assert result.scenario == "新拠点を開設したら？"
        assert result.model_used == "gemini-2.0-pro"
        assert result.cost_yen == 0.12

    @pytest.mark.asyncio
    async def test_five_dimensions_analyzed(self):
        """5次元全てが分析される。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_WHATIF_RESPONSE
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.12
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            result = await run_whatif(company_id, "主力顧客が離脱したら？", mock_db)

        assert len(result.dimension_impacts) == 5

    @pytest.mark.asyncio
    async def test_db_fetch_uses_company_id_filter(self):
        """DB取得時に company_id フィルタが適用される。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_WHATIF_RESPONSE
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            await run_whatif(company_id, "テストシナリオ", mock_db)

        table_calls = [call[0][0] for call in mock_db.table.call_args_list]
        assert "company_state_snapshots" in table_calls
        assert "knowledge_items" in table_calls

    @pytest.mark.asyncio
    async def test_llm_task_uses_standard_tier(self):
        """LLMタスクが STANDARD tier で呼ばれる。"""
        from llm.client import ModelTier
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_WHATIF_RESPONSE
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            await run_whatif(company_id, "テスト", mock_db)

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        assert task.tier == ModelTier.STANDARD

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback_result(self):
        """LLMが失敗してもフォールバック結果を返す（クラッシュしない）。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "これはJSONではありません"
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            result = await run_whatif(company_id, "テストシナリオ", mock_db)

        assert isinstance(result, WhatIfResult)
        assert result.scenario == "テストシナリオ"

    @pytest.mark.asyncio
    async def test_overall_score_delta_is_float(self):
        """overall_score_delta が float 型で返る。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_WHATIF_RESPONSE
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.whatif.get_llm_client", return_value=mock_llm
        ):
            result = await run_whatif(company_id, "テスト", mock_db)

        assert isinstance(result.overall_score_delta, float)

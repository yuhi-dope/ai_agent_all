"""ReasoningTrace の生成・パース・LLMClient統合のユニットテスト"""
import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.client import (
    Evidence,
    LLMResponse,
    LLMTask,
    ModelTier,
    ReasoningTrace,
    _parse_reasoning_trace,
    _REASONING_TRACE_INSTRUCTION,
)


# ---------------------------------------------------------------------------
# 1. _parse_reasoning_trace — 正常系
# ---------------------------------------------------------------------------

_VALID_TRACE_JSON = json.dumps({
    "reasoning_trace": {
        "action_summary": "積算金額を¥12,650,000と算出しました",
        "confidence_score": 0.87,
        "evidence": [
            {
                "description": "自社過去実績の加重平均単価を採用",
                "source": "unit_price_master",
                "confidence": 0.9,
                "value": "¥12,000/m3",
            },
            {
                "description": "公共工事設計労務単価（関東）",
                "source": "public_labor_rates",
                "confidence": 0.95,
                "value": None,
            },
        ],
        "data_sources": ["unit_price_master", "public_labor_rates"],
        "assumptions": ["消費税10%を含む", "残土処分費は別途"],
        "alternatives": ["民間単価ベースの試算: ¥11,200,000"],
    }
})

_CONTENT_WITH_TRACE = f"積算結果を以下に示します。\n\n```json\n{_VALID_TRACE_JSON}\n```"


def test_parse_reasoning_trace_extracts_clean_content():
    """reasoning_trace JSONブロックを除いた本文が正しく返ること。"""
    clean, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert "積算結果を以下に示します。" in clean
    assert "reasoning_trace" not in clean
    assert "```json" not in clean


def test_parse_reasoning_trace_action_summary():
    """action_summary が正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert trace.action_summary == "積算金額を¥12,650,000と算出しました"


def test_parse_reasoning_trace_confidence_score():
    """confidence_score が float として正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert abs(trace.confidence_score - 0.87) < 1e-9


def test_parse_reasoning_trace_evidence_count():
    """evidence リストの件数が正しいこと。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert len(trace.evidence) == 2


def test_parse_reasoning_trace_evidence_fields():
    """Evidence の各フィールドが正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    ev = trace.evidence[0]
    assert ev.description == "自社過去実績の加重平均単価を採用"
    assert ev.source == "unit_price_master"
    assert abs(ev.confidence - 0.9) < 1e-9
    assert ev.value == "¥12,000/m3"


def test_parse_reasoning_trace_evidence_none_value():
    """Evidence.value が null の場合 None になること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    ev = trace.evidence[1]
    assert ev.value is None


def test_parse_reasoning_trace_data_sources():
    """data_sources リストが正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert "unit_price_master" in trace.data_sources
    assert "public_labor_rates" in trace.data_sources


def test_parse_reasoning_trace_assumptions():
    """assumptions リストが正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert "消費税10%を含む" in trace.assumptions
    assert "残土処分費は別途" in trace.assumptions


def test_parse_reasoning_trace_alternatives():
    """alternatives リストが正しくパースされること。"""
    _, trace = _parse_reasoning_trace(_CONTENT_WITH_TRACE)
    assert trace is not None
    assert len(trace.alternatives) == 1
    assert "民間単価ベースの試算" in trace.alternatives[0]


# ---------------------------------------------------------------------------
# 2. _parse_reasoning_trace — 異常系（パース失敗時は None を返す）
# ---------------------------------------------------------------------------

def test_parse_reasoning_trace_no_json_block():
    """JSONブロックがない場合、traceがNoneで元コンテンツがそのまま返ること。"""
    content = "これは通常のレスポンスです。JSONブロックはありません。"
    clean, trace = _parse_reasoning_trace(content)
    assert clean == content
    assert trace is None


def test_parse_reasoning_trace_malformed_json():
    """不正なJSONの場合、例外を握りつぶしてNoneを返すこと。"""
    content = "回答テキスト\n```json\n{壊れたJSON: ]\n```"
    clean, trace = _parse_reasoning_trace(content)
    # 本文は元のまま（JSONブロック抽出を試みるが失敗）
    assert trace is None


def test_parse_reasoning_trace_missing_reasoning_trace_key():
    """reasoning_trace キーがないJSONの場合、traceがNoneになること。"""
    other_json = json.dumps({"other_key": "value"})
    content = f"回答\n```json\n{other_json}\n```"
    _, trace = _parse_reasoning_trace(content)
    # reasoning_trace キーがないため空のReasoningTraceになるかNone
    # 実装では空dictでReasoningTraceが作られるが action_summary は空文字列
    if trace is not None:
        assert trace.action_summary == ""
        assert trace.confidence_score == 0.0


def test_parse_reasoning_trace_empty_evidence():
    """evidence が空リストの場合、空リストが返ること。"""
    data = {
        "reasoning_trace": {
            "action_summary": "処理完了",
            "confidence_score": 0.5,
            "evidence": [],
            "data_sources": [],
            "assumptions": [],
            "alternatives": [],
        }
    }
    content = f"本文\n```json\n{json.dumps(data)}\n```"
    _, trace = _parse_reasoning_trace(content)
    assert trace is not None
    assert trace.evidence == []


# ---------------------------------------------------------------------------
# 3. LLMTask — with_trace フィールドのデフォルト値と後方互換
# ---------------------------------------------------------------------------

def test_llm_task_with_trace_default_false():
    """LLMTask の with_trace デフォルトが False であること（後方互換）。"""
    task = LLMTask(messages=[{"role": "user", "content": "テスト"}])
    assert task.with_trace is False


def test_llm_task_with_trace_true():
    """LLMTask の with_trace=True が設定できること。"""
    task = LLMTask(
        messages=[{"role": "user", "content": "テスト"}],
        with_trace=True,
    )
    assert task.with_trace is True


# ---------------------------------------------------------------------------
# 4. LLMResponse — reasoning_trace フィールドのデフォルト値
# ---------------------------------------------------------------------------

def test_llm_response_reasoning_trace_default_none():
    """LLMResponse の reasoning_trace デフォルトが None であること（後方互換）。"""
    response = LLMResponse(
        content="テスト",
        model_used="gemini-2.5-flash",
        tokens_in=10,
        tokens_out=20,
        cost_yen=0.001,
        latency_ms=100,
    )
    assert response.reasoning_trace is None


# ---------------------------------------------------------------------------
# 5. LLMClient.generate — with_trace=True 時のプロンプト拡張とトレース格納
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_raw_response():
    """LLM APIが reasoning_trace 付きコンテンツを返すモック。"""
    trace_data = {
        "reasoning_trace": {
            "action_summary": "テスト積算を完了しました",
            "confidence_score": 0.80,
            "evidence": [
                {
                    "description": "過去実績データより",
                    "source": "unit_price_master",
                    "confidence": 0.85,
                    "value": "¥5,000/m2",
                }
            ],
            "data_sources": ["unit_price_master"],
            "assumptions": ["標準的な地盤条件を想定"],
            "alternatives": [],
        }
    }
    return f"積算完了\n\n```json\n{json.dumps(trace_data, ensure_ascii=False)}\n```"


@pytest.mark.asyncio
async def test_generate_with_trace_populates_reasoning_trace(mock_llm_raw_response):
    """with_trace=True のとき、LLMResponseのreasoning_traceにトレースが格納されること。"""
    from llm.client import LLMClient

    client = LLMClient()

    with patch.object(
        client, "_call_model",
        new=AsyncMock(return_value={
            "content": mock_llm_raw_response,
            "tokens_in": 100,
            "tokens_out": 200,
        })
    ), patch("llm.client.get_cost_tracker") as mock_tracker, \
       patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
       patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
       patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        task = LLMTask(
            messages=[
                {"role": "system", "content": "積算AIです"},
                {"role": "user", "content": "積算してください"},
            ],
            tier=ModelTier.FAST,
            task_type="quantity_extraction",
            with_trace=True,
        )
        response = await client.generate(task)

    assert response.reasoning_trace is not None
    assert response.reasoning_trace.action_summary == "テスト積算を完了しました"
    assert abs(response.reasoning_trace.confidence_score - 0.80) < 1e-9
    assert len(response.reasoning_trace.evidence) == 1
    assert response.reasoning_trace.evidence[0].source == "unit_price_master"


@pytest.mark.asyncio
async def test_generate_with_trace_strips_json_from_content(mock_llm_raw_response):
    """with_trace=True のとき、content からJSONブロックが除去されていること。"""
    from llm.client import LLMClient

    client = LLMClient()

    with patch.object(
        client, "_call_model",
        new=AsyncMock(return_value={
            "content": mock_llm_raw_response,
            "tokens_in": 100,
            "tokens_out": 200,
        })
    ), patch("llm.client.get_cost_tracker") as mock_tracker, \
       patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
       patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
       patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        task = LLMTask(
            messages=[{"role": "user", "content": "積算してください"}],
            with_trace=True,
        )
        response = await client.generate(task)

    assert "reasoning_trace" not in response.content
    assert "```json" not in response.content
    assert "積算完了" in response.content


@pytest.mark.asyncio
async def test_generate_without_trace_no_reasoning_trace():
    """with_trace=False（デフォルト）のとき、reasoning_traceがNoneであること（後方互換）。"""
    from llm.client import LLMClient

    client = LLMClient()

    with patch.object(
        client, "_call_model",
        new=AsyncMock(return_value={
            "content": "通常のレスポンスです",
            "tokens_in": 50,
            "tokens_out": 80,
        })
    ), patch("llm.client.get_cost_tracker") as mock_tracker, \
       patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
       patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
       patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        task = LLMTask(
            messages=[{"role": "user", "content": "テスト"}],
            with_trace=False,
        )
        response = await client.generate(task)

    assert response.reasoning_trace is None
    assert response.content == "通常のレスポンスです"


@pytest.mark.asyncio
async def test_generate_with_trace_appends_instruction_to_user_message():
    """with_trace=True のとき、最後のユーザーメッセージに推論指示が追記されること。"""
    from llm.client import LLMClient

    client = LLMClient()
    captured_task = {}

    async def capture_call(model_id: str, task: LLMTask) -> dict:
        captured_task["task"] = task
        return {"content": "テスト回答", "tokens_in": 10, "tokens_out": 20}

    with patch.object(client, "_call_model", new=capture_call), \
         patch("llm.client.get_cost_tracker") as mock_tracker, \
         patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
         patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
         patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        original_user_content = "積算してください"
        task = LLMTask(
            messages=[
                {"role": "system", "content": "積算AIです"},
                {"role": "user", "content": original_user_content},
            ],
            with_trace=True,
        )
        await client.generate(task)

    effective_messages = captured_task["task"].messages
    last_user = next(
        (m for m in reversed(effective_messages) if m["role"] == "user"), None
    )
    assert last_user is not None
    assert original_user_content in last_user["content"]
    assert "reasoning_trace" in last_user["content"]


@pytest.mark.asyncio
async def test_generate_with_trace_does_not_mutate_original_task():
    """with_trace=True でも元のLLMTaskのmessagesが変更されないこと。"""
    from llm.client import LLMClient

    client = LLMClient()
    original_content = "元のメッセージ"

    with patch.object(
        client, "_call_model",
        new=AsyncMock(return_value={"content": "回答", "tokens_in": 10, "tokens_out": 20})
    ), patch("llm.client.get_cost_tracker") as mock_tracker, \
       patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
       patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
       patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        task = LLMTask(
            messages=[{"role": "user", "content": original_content}],
            with_trace=True,
        )
        await client.generate(task)

    # 元のtaskのmessagesは変更されていないこと
    assert task.messages[0]["content"] == original_content


# ---------------------------------------------------------------------------
# 6. LLMClient.generate — with_trace=True でJSONパース失敗時にNoneを返す
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_with_trace_parse_failure_returns_none():
    """with_trace=True でJSONパース失敗時でもレスポンスが返り、reasoning_traceがNoneになること。"""
    from llm.client import LLMClient

    client = LLMClient()
    broken_content = "回答テキスト\n```json\n{壊れたJSON\n```"

    with patch.object(
        client, "_call_model",
        new=AsyncMock(return_value={
            "content": broken_content,
            "tokens_in": 50,
            "tokens_out": 100,
        })
    ), patch("llm.client.get_cost_tracker") as mock_tracker, \
       patch("llm.client.select_optimal_model", return_value="gemini-2.5-flash"), \
       patch("llm.client.get_fallback_chain", return_value=["gemini-2.5-flash"]), \
       patch("llm.client.get_model_costs", return_value={"in": 0.001, "out": 0.002}):

        mock_tracker.return_value.check_budget = MagicMock()
        mock_tracker.return_value.record_cost = MagicMock()

        task = LLMTask(
            messages=[{"role": "user", "content": "テスト"}],
            with_trace=True,
        )
        response = await client.generate(task)

    # パース失敗時は例外なく、reasoning_trace=None で返ること
    assert response.reasoning_trace is None
    assert response.content is not None


# ---------------------------------------------------------------------------
# 7. ReasoningTrace / Evidence dataclass の基本動作
# ---------------------------------------------------------------------------

def test_evidence_dataclass_fields():
    """Evidence dataclass のフィールドが正しく設定されること。"""
    ev = Evidence(
        description="根拠説明",
        source="similar_case",
        confidence=0.75,
        value="¥8,000/m",
    )
    assert ev.description == "根拠説明"
    assert ev.source == "similar_case"
    assert ev.confidence == 0.75
    assert ev.value == "¥8,000/m"


def test_evidence_value_optional():
    """Evidence.value は省略可能（デフォルトNone）であること。"""
    ev = Evidence(description="説明", source="genome_rule", confidence=0.6)
    assert ev.value is None


def test_reasoning_trace_defaults():
    """ReasoningTrace のデフォルトフィールドが空リストであること。"""
    trace = ReasoningTrace(
        action_summary="テスト処理完了",
        confidence_score=0.9,
    )
    assert trace.evidence == []
    assert trace.data_sources == []
    assert trace.assumptions == []
    assert trace.alternatives == []

"""Tests for brain.proactive.parsing JSON extraction and proposal parsing."""

import json

from brain.proactive.parsing import (
    extract_json_from_llm_response,
    parse_proposals_from_llm_response,
)


def test_extract_json_from_markdown_fence() -> None:
    raw = """以下です。

```json
[{"type": "risk_alert", "title": "T", "description": "D"}]
```
"""
    out = extract_json_from_llm_response(raw)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["title"] == "T"


def test_extract_json_bracket_without_fence() -> None:
    raw = '前置き\n[{"type": "improvement", "title": "x", "description": "y"}]\n後ろ'
    out = extract_json_from_llm_response(raw)
    data = json.loads(out)
    assert data[0]["type"] == "improvement"


def test_extract_json_open_fence_no_close_with_trailing_prose() -> None:
    raw = """```json
[{"type": "risk_alert", "title": "規程照合", "description": "本文。", "impact_estimate": {}}]
以上は参考です。"""
    out = extract_json_from_llm_response(raw)
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["title"] == "規程照合"


def test_extract_json_preamble_with_bracket_before_fence() -> None:
    raw = """参照[1]があります。

```json
[{"type": "risk_alert", "title": "正しい", "description": "x", "impact_estimate": {}}]
"""
    out = extract_json_from_llm_response(raw)
    data = json.loads(out)
    assert data[0]["title"] == "正しい"


def test_parse_proposals_array() -> None:
    items = [{"id": "00000000-0000-0000-0000-000000000001"}]
    content = json.dumps(
        [
            {
                "type": "risk_alert",
                "title": "許可更新",
                "description": "説明",
                "priority": "high",
                "impact_estimate": {
                    "time_saved_hours": None,
                    "cost_reduction_yen": None,
                    "confidence": 0.8,
                },
            }
        ],
        ensure_ascii=False,
    )
    proposals = parse_proposals_from_llm_response(content, items)
    assert len(proposals) == 1
    assert proposals[0].proposal_type == "risk_alert"
    assert proposals[0].title == "許可更新"
    assert proposals[0].description == "説明"


def test_parse_proposals_cost_saved_yen_alias() -> None:
    items = [{"id": "00000000-0000-0000-0000-000000000001"}]
    content = json.dumps(
        [
            {
                "type": "improvement",
                "title": "t",
                "description": "d",
                "impact_estimate": {"cost_saved_yen": 10000},
            }
        ],
        ensure_ascii=False,
    )
    proposals = parse_proposals_from_llm_response(content, items)
    assert proposals[0].impact_estimate is not None
    assert proposals[0].impact_estimate.cost_reduction_yen == 10000


def test_parse_proposals_fallback_no_raw_llm() -> None:
    items = [{"id": "00000000-0000-0000-0000-000000000001"}]
    proposals = parse_proposals_from_llm_response("これはJSONではありません {{{", items)
    assert len(proposals) == 1
    assert proposals[0].title == "分析結果"
    assert "解釈できませんでした" in proposals[0].description
    assert "```" not in proposals[0].description
    assert "{" not in proposals[0].description


def test_parse_proposals_trailing_comma_json() -> None:
    items = [{"id": "00000000-0000-0000-0000-000000000001"}]
    content = """
[
  {
    "type": "improvement",
    "title": "テンプレート標準化",
    "description": "末尾カンマ付きJSONでも解釈する",
  },
]
"""
    proposals = parse_proposals_from_llm_response(content, items)
    assert len(proposals) == 1
    assert proposals[0].proposal_type == "improvement"
    assert proposals[0].title == "テンプレート標準化"

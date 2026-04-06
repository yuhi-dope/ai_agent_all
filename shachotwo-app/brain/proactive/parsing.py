"""LLM応答から能動提案をパースする（LLMクライアントに依存しない）。"""
from __future__ import annotations

import json
import logging
import re
from uuid import UUID

import json5
from json_repair import repair_json
from brain.proactive.models import Evidence, ImpactEstimate, Proposal, Signal

logger = logging.getLogger(__name__)


def _normalize_fences(text: str) -> str:
    """全角バッククォート U+FF40 を ASCII ` に寄せる（LLM のフェンス検出用）。"""
    return text.replace("\ufeff", "").replace("\uff40", "`")


def _normalize_smart_quotes_for_json_balance(text: str) -> str:
    """TS の normalizeSmartQuotesForJsonBalance と同等。"""
    return (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u201e", '"')
        .replace("\u201f", '"')
        .replace("\u2033", '"')
        .replace("\u2036", '"')
    )


def _extract_first_balanced_json(text: str) -> str | None:
    """ダブル／シングル文字列と // /* */ コメントを無視した括弧バランス（TS extractFirstBalancedJsonValue と同等）。"""
    t = _normalize_smart_quotes_for_json_balance(text).strip()
    br, ob = t.find("["), t.find("{")
    if br == -1 and ob == -1:
        return None
    candidates = [i for i in (br, ob) if i >= 0]
    start = min(candidates)

    stack: list[str] = []
    in_double = False
    in_single = False
    escape = False
    in_line_comment = False
    in_block_comment = False

    i = start
    while i < len(t):
        c = t[i]
        if in_line_comment:
            if c in "\n\r":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if c == "*" and i + 1 < len(t) and t[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if escape:
            escape = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                escape = True
                i += 1
                continue
            if c == '"':
                in_double = False
                i += 1
                continue
            i += 1
            continue
        if in_single:
            if c == "\\":
                escape = True
                i += 1
                continue
            if c == "'":
                in_single = False
                i += 1
                continue
            i += 1
            continue
        if c == "/" and i + 1 < len(t) and t[i + 1] == "/":
            in_line_comment = True
            i += 2
            continue
        if c == "/" and i + 1 < len(t) and t[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == "[":
            stack.append("]")
        elif c == "{":
            stack.append("}")
        elif c in "]}":
            if stack and stack[-1] == c:
                stack.pop()
                if not stack:
                    return t[start : i + 1]
        i += 1
    return None


def _try_extract_json_by_trailing_json5_parse(text: str) -> str | None:
    t = _normalize_smart_quotes_for_json_balance(text).strip()
    br, ob = t.find("["), t.find("{")
    if br == -1 and ob == -1:
        return None
    candidates = [i for i in (br, ob) if i >= 0]
    start = min(candidates)
    open_ch = t[start]
    close = "]" if open_ch == "[" else "}"
    i = len(t) - 1
    while i > start:
        if t[i] != close:
            i -= 1
            continue
        slice_s = t[start : i + 1]
        try:
            json5.loads(slice_s)
            return slice_s
        except (ValueError, TypeError):
            pass
        i -= 1
    return None


def _slice_from_first_json_bracket(text: str) -> str | None:
    t = _normalize_smart_quotes_for_json_balance(text).strip()
    br, ob = t.find("["), t.find("{")
    if br == -1 and ob == -1:
        return None
    start = min(i for i in (br, ob) if i >= 0)
    return t[start:]


def _try_extract_json_by_json_repair(text: str) -> str | None:
    candidate = _slice_from_first_json_bracket(text)
    if not candidate:
        return None
    try:
        repaired = repair_json(candidate)
        data = json5.loads(repaired)
        if data is None or not isinstance(data, (dict, list)):
            return None
        if isinstance(data, list) and len(data) == 0:
            return None
        if isinstance(data, dict) and len(data) == 0:
            return None
        return repaired
    except (ValueError, TypeError, AttributeError):
        return None


def _slice_after_fence_start(text: str) -> str | None:
    m = re.search(r"```(?:json)?\s*\n?", text, re.IGNORECASE)
    if not m:
        return None
    return text[m.end() :]


def extract_json_from_llm_response(content: str) -> str:
    """Extract JSON from LLM response, handling markdown fences and surrounding text (TS extractJsonFromText と同等)."""
    text = _normalize_fences(content).strip()

    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()

    after_open = _slice_after_fence_start(text)

    if after_open is not None:
        from_fence = _extract_first_balanced_json(after_open)
        if from_fence:
            return from_fence
        trail_fence = _try_extract_json_by_trailing_json5_parse(after_open)
        if trail_fence:
            return trail_fence
        repair_fence = _try_extract_json_by_json_repair(after_open)
        if repair_fence:
            return repair_fence

    if after_open is None:
        balanced_body = _extract_first_balanced_json(text)
        if balanced_body:
            return balanced_body
        trail_full = _try_extract_json_by_trailing_json5_parse(text)
        if trail_full:
            return trail_full
        repair_full = _try_extract_json_by_json_repair(text)
        if repair_full:
            return repair_full

    naive_source = after_open if after_open is not None else text
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = naive_source.find(start_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(naive_source)):
            if naive_source[i] == start_char:
                depth += 1
            elif naive_source[i] == end_char:
                depth -= 1
                if depth == 0:
                    return naive_source[start : i + 1]

    return text


def parse_proposals_from_llm_response(content: str, items: list[dict]) -> list[Proposal]:
    """Parse LLM response into Proposal list."""
    try:
        text = _normalize_smart_quotes_for_json_balance(extract_json_from_llm_response(content))
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = json5.loads(text)
        if not isinstance(data, list):
            data = [data]

        item_ids = [UUID(it["id"]) for it in items]

        proposals = []
        for raw in data:
            impact = None
            if raw.get("impact_estimate"):
                ie = raw["impact_estimate"]
                cost_yen = ie.get("cost_reduction_yen")
                if cost_yen is None:
                    cost_yen = ie.get("cost_saved_yen")
                impact = ImpactEstimate(
                    time_saved_hours=ie.get("time_saved_hours"),
                    cost_reduction_yen=cost_yen,
                    risk_reduction=ie.get("risk_reduction"),
                    confidence=ie.get("confidence", 0.5),
                    calculation_basis=ie.get("calculation_basis"),
                )

            evidence = None
            if raw.get("evidence") and raw["evidence"].get("signals"):
                evidence = Evidence(
                    signals=[
                        Signal(
                            source=s.get("source", "knowledge"),
                            value=s.get("value", ""),
                            score=s.get("score", 0.5),
                        )
                        for s in raw["evidence"]["signals"]
                    ]
                )

            proposals.append(
                Proposal(
                    proposal_type=raw.get("type", "improvement"),
                    title=raw.get("title", ""),
                    description=raw.get("description", ""),
                    impact_estimate=impact,
                    evidence=evidence,
                    priority=raw.get("priority", "medium"),
                    related_knowledge_ids=item_ids[:5],
                )
            )

        return proposals

    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
        logger.warning(
            "Proactive response parse failed: %s, using user-facing fallback (raw excerpt in log)",
            e,
        )
        logger.debug("Proactive raw response excerpt: %s", (content or "")[:1500])
        return [
            Proposal(
                proposal_type="improvement",
                title="分析結果",
                description=(
                    "分析結果の形式を自動で解釈できませんでした。"
                    "ナレッジを整理したうえで、もう一度「AI分析を開始する」をお試しください。"
                ),
                priority="medium",
            )
        ]

"""anomaly_detector マイクロエージェント。数値の異常値・外れ値・桁間違いを統計ベースで検知する。LLM不使用。"""
import time
import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)

AGENT_NAME = "anomaly_detector"

_OPERATORS = {
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "gt":  lambda a, b: a > b,
    "lt":  lambda a, b: a < b,
    "eq":  lambda a, b: a == b,
    "ne":  lambda a, b: a != b,
}


def _to_decimal(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError) as e:
        raise ValueError(f"数値に変換できません: {v}") from e


def _fmt_yen(v: Decimal) -> str:
    return f"¥{int(v):,}"


def _check_range(
    name: str,
    value: Decimal,
    expected_range: list,
) -> dict | None:
    """expected_range = [min, max] の範囲外なら異常を返す。"""
    if len(expected_range) != 2:
        return None
    lo = _to_decimal(expected_range[0])
    hi = _to_decimal(expected_range[1])
    if lo <= value <= hi:
        return None

    # 外れ具合でseverityを決定
    span = hi - lo
    if span == 0:
        ratio = Decimal("999")
    elif value < lo:
        ratio = (lo - value) / span
    else:
        ratio = (value - hi) / span

    if ratio >= Decimal("1"):  # 範囲幅の2倍以上外れている
        severity = "high"
    elif ratio >= Decimal("0.5"):
        severity = "medium"
    else:
        severity = "low"

    if value < lo:
        msg = f"{name}({_fmt_yen(value)})は期待範囲の下限({_fmt_yen(lo)})を下回っています"
    else:
        msg = f"{name}({_fmt_yen(value)})は期待範囲の上限({_fmt_yen(hi)})を超えています"

    return {
        "field": name,
        "value": int(value),
        "type": "range",
        "severity": severity,
        "message": msg,
        "suggestion": f"期待範囲: {_fmt_yen(lo)} 〜 {_fmt_yen(hi)}",
    }


def _check_zscore(
    name: str,
    value: Decimal,
    historical: list,
) -> dict | None:
    """Z-score > 2.5 なら異常を返す。標本数が2未満の場合はスキップ。"""
    if len(historical) < 2:
        return None

    h_dec = [_to_decimal(v) for v in historical]
    n = len(h_dec)
    mean = sum(h_dec) / n
    variance = sum((v - mean) ** 2 for v in h_dec) / n
    if variance == 0:
        # 全て同じ値で分散ゼロ — 一致しなければ異常とみなす
        if value != mean:
            return {
                "field": name,
                "value": int(value),
                "type": "zscore",
                "severity": "high",
                "message": f"{name}({_fmt_yen(value)})は過去実績(全て{_fmt_yen(mean)})と異なります",
                "suggestion": f"過去の平均値: {_fmt_yen(mean)}",
            }
        return None

    std = variance.sqrt()
    zscore = abs(value - mean) / std

    if zscore <= Decimal("2.5"):
        return None

    severity = "high" if zscore > Decimal("3") else "medium"
    msg = (
        f"{name}({_fmt_yen(value)})のZ-scoreが{float(zscore):.2f}です"
        f"（過去平均: {_fmt_yen(mean)}, 標準偏差: {_fmt_yen(std)}）"
    )
    return {
        "field": name,
        "value": int(value),
        "type": "zscore",
        "severity": severity,
        "message": msg,
        "suggestion": f"過去の平均値: {_fmt_yen(mean)}",
    }


def _check_digit_error(
    name: str,
    value: Decimal,
    other_values: list[Decimal],
) -> dict | None:
    """他項目の平均と比べて10倍以上 / 1/10以下なら桁間違い疑い。"""
    if not other_values:
        return None

    mean_others = sum(other_values) / len(other_values)
    if mean_others == 0:
        return None

    ratio = value / mean_others

    if ratio < Decimal("0.1") or ratio >= Decimal("10"):
        # 推測値: ratioが大きければ1/10、小さければ10倍
        if ratio >= Decimal("10"):
            suggestion_val = value / Decimal("10")
            direction = "大きすぎる"
        else:
            suggestion_val = value * Decimal("10")
            direction = "小さすぎる"

        msg = (
            f"{name}({_fmt_yen(value)})は他項目の平均({_fmt_yen(mean_others)})の"
            f"{float(ratio):.1f}倍です。桁間違いの可能性（{direction}）"
        )
        return {
            "field": name,
            "value": int(value),
            "type": "digit_error",
            "severity": "high",
            "message": msg,
            "suggestion": f"{_fmt_yen(suggestion_val)} ではありませんか？",
        }
    return None


def _check_rules(
    name: str,
    value: Decimal,
    rules: list[dict],
) -> list[dict]:
    """カスタムルールに違反した項目を全て返す。"""
    violations = []
    for rule in rules:
        if rule.get("field") != name:
            continue
        operator = rule.get("operator", "")
        threshold = rule.get("threshold")
        custom_msg = rule.get("message", "")
        if operator not in _OPERATORS or threshold is None:
            continue
        try:
            thr = _to_decimal(threshold)
        except ValueError:
            continue
        if not _OPERATORS[operator](value, thr):
            msg = custom_msg or (
                f"{name}({_fmt_yen(value)})がルール違反です"
                f"（{operator} {_fmt_yen(thr)}）"
            )
            violations.append({
                "field": name,
                "value": int(value),
                "type": "rule_violation",
                "severity": "medium",
                "message": msg,
                "suggestion": f"閾値: {operator} {_fmt_yen(thr)}",
            })
    return violations


async def run_anomaly_detector(inp: MicroAgentInput) -> MicroAgentOutput:
    """
    数値の異常値・外れ値・桁間違いを統計ベースで検知する（LLM不使用）。

    payload:
        items (list[dict]): 検査対象。各要素に name, value, expected_range(optional)
        rules (list[dict], optional): カスタムルール（field/operator/threshold/message）
        historical_values (dict, optional): 過去実績 {フィールド名: [値, ...]}
        detect_modes (list[str], optional): 使用する検知モード
            ["range", "zscore", "digit_error", "rules"]

    result:
        anomalies (list[dict]): 検知した異常のリスト
        total_checked (int): チェックしたアイテム数
        anomaly_count (int): 異常件数
        passed (bool): 異常ゼロかどうか
    """
    start_ms = int(time.time() * 1000)

    try:
        items: list[dict] = inp.payload.get("items", [])
        rules: list[dict] = inp.payload.get("rules", [])
        historical_values: dict[str, list] = inp.payload.get("historical_values", {})
        detect_modes: list[str] = inp.payload.get(
            "detect_modes", ["range", "zscore", "digit_error", "rules"]
        )

        if not items:
            raise MicroAgentError(AGENT_NAME, "parse", "items が空です")

        # 各アイテムをDecimalに変換
        parsed: list[tuple[str, Decimal, dict]] = []
        for item in items:
            name = item.get("name", "")
            if not name:
                raise MicroAgentError(AGENT_NAME, "parse", "items[].name が未指定です")
            try:
                value = _to_decimal(item["value"])
            except (KeyError, ValueError) as e:
                raise MicroAgentError(AGENT_NAME, "parse", f"{name}: {e}") from e
            parsed.append((name, value, item))

        anomalies: list[dict] = []

        # digit_error 用: 全アイテムの値リスト
        all_values: list[Decimal] = [v for _, v, _ in parsed]

        for name, value, item in parsed:
            # 1. range チェック
            if "range" in detect_modes:
                expected_range = item.get("expected_range")
                if expected_range:
                    result = _check_range(name, value, expected_range)
                    if result:
                        anomalies.append(result)

            # 2. zscore チェック
            if "zscore" in detect_modes:
                hist = historical_values.get(name)
                if hist:
                    result = _check_zscore(name, value, hist)
                    if result:
                        anomalies.append(result)

            # 3. digit_error チェック（自分以外の全アイテムの平均と比較）
            if "digit_error" in detect_modes:
                others = [v for n, v, _ in parsed if n != name]
                if others:
                    result = _check_digit_error(name, value, others)
                    if result:
                        anomalies.append(result)

            # 4. rules チェック
            if "rules" in detect_modes and rules:
                violations = _check_rules(name, value, rules)
                anomalies.extend(violations)

        anomaly_count = len(anomalies)
        passed = anomaly_count == 0

        # confidence: 異常0件なら1.0、1件以上なら 1.0 - (count * 0.2) で最低0.1
        confidence = max(0.1, 1.0 - anomaly_count * 0.2) if anomaly_count > 0 else 1.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=AGENT_NAME,
            success=True,
            result={
                "anomalies": anomalies,
                "total_checked": len(parsed),
                "anomaly_count": anomaly_count,
                "passed": passed,
            },
            confidence=round(confidence, 3),
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"{AGENT_NAME} error: {e}")
        return MicroAgentOutput(
            agent_name=AGENT_NAME,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

"""
run 単位の LLM トークン使用量から概算コストを計算し、予算閾値と比較する。
Vertex AI Gemini の概算単価（Pro 相当で保守的に）を使用。環境変数で上書き可能。
"""

import os


# Vertex AI Gemini 概算（Pro 相当: $1.25/1M input, $10/1M output）
_DEFAULT_INPUT_PER_MILLION = 1.25
_DEFAULT_OUTPUT_PER_MILLION = 10.0


def get_cost_per_million() -> tuple[float, float]:
    """(input $/1M tokens, output $/1M tokens) を返す。環境変数 COST_INPUT_PER_MILLION, COST_OUTPUT_PER_MILLION で上書き可能。"""
    try:
        inp = float(os.environ.get("COST_INPUT_PER_MILLION", _DEFAULT_INPUT_PER_MILLION))
    except (TypeError, ValueError):
        inp = _DEFAULT_INPUT_PER_MILLION
    try:
        out = float(os.environ.get("COST_OUTPUT_PER_MILLION", _DEFAULT_OUTPUT_PER_MILLION))
    except (TypeError, ValueError):
        out = _DEFAULT_OUTPUT_PER_MILLION
    return inp, out


def get_max_cost_per_task_usd() -> float:
    """1 task (1 run) あたりの予算上限 USD。環境変数 MAX_COST_PER_TASK_USD で上書き。デフォルト 0.5。"""
    try:
        return float(os.environ.get("MAX_COST_PER_TASK_USD", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def estimate_cost_usd(total_input_tokens: int, total_output_tokens: int) -> float:
    """入力・出力トークン数から概算コスト（USD）を返す。"""
    inp_per_m, out_per_m = get_cost_per_million()
    return (total_input_tokens / 1_000_000) * inp_per_m + (total_output_tokens / 1_000_000) * out_per_m


def check_budget(
    total_input_tokens: int, total_output_tokens: int
) -> tuple[float, bool]:
    """
    トークン数から概算コストを算出し、予算上限を超えているか返す。
    戻り値: (estimated_cost_usd, budget_exceeded).
    """
    cost = estimate_cost_usd(total_input_tokens, total_output_tokens)
    max_cost = get_max_cost_per_task_usd()
    return cost, cost > max_cost

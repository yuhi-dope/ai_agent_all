"""結果レポートノード: 実行結果をサマリー化。

企業データ（API レスポンス全文）は保存せず、成功/失敗件数とエラーメッセージのみを記録する。
失敗があった場合は failure_reason/failure_category を設定し、学習システムが参照できるようにする。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import BPOState

logger = logging.getLogger(__name__)

# 失敗カテゴリの分類ルール
_FAILURE_CATEGORIES = {
    "auth": "auth_error",
    "unauthorized": "auth_error",
    "token": "auth_error",
    "expired": "auth_error",
    "validation": "validation_error",
    "invalid": "validation_error",
    "required": "validation_error",
    "missing": "validation_error",
    "rate_limit": "rate_limit",
    "too many": "rate_limit",
    "throttl": "rate_limit",
    "timeout": "timeout",
    "timed out": "timeout",
}


def result_reporter_node(state: BPOState) -> dict[str, Any]:
    """実行結果をサマリー化するノード。

    企業の機密データ（API レスポンス全文）は保存しない。
    成功/失敗件数 + エラーメッセージのみをサマリーとして返す。
    """
    results = state.get("saas_results") or []

    if not results:
        return {
            "saas_results": [{"success_count": 0, "failure_count": 0, "total_operations": 0, "errors": []}],
            "saas_report_markdown": "操作なし",
            "status": "completed",
        }

    # サマリー計算
    success_count = sum(1 for r in results if r.get("result", {}).get("success", False))
    failure_count = len(results) - success_count
    errors = [
        f"{r.get('tool_name', 'unknown')}: {r.get('result', {}).get('error', 'unknown error')}"
        for r in results
        if not r.get("result", {}).get("success", False)
    ]

    summary = {
        "success_count": success_count,
        "failure_count": failure_count,
        "total_operations": len(results),
        "errors": errors[:10],
    }

    # マークダウンレポート生成
    report = _build_report_markdown(results, summary)

    # ステータス判定
    if failure_count == 0:
        status = "completed"
    elif success_count > 0:
        status = "completed"  # 部分成功もcompletedだがsummaryで区別可能
    else:
        status = "failed"

    out: dict[str, Any] = {
        "saas_results": [summary],
        "saas_report_markdown": report,
        "status": status,
    }

    # 失敗情報を学習システム用に設定
    if failure_count > 0:
        out["failure_reason"] = errors[0] if errors else "unknown"
        out["failure_category"] = _categorize_failure(errors)

    return out


def _build_report_markdown(results: list[dict], summary: dict) -> str:
    """結果をマークダウン形式のレポートにする。"""
    lines = ["## 実行結果レポート", ""]
    lines.append(f"- 成功: {summary['success_count']}件")
    lines.append(f"- 失敗: {summary['failure_count']}件")
    lines.append(f"- 合計: {summary['total_operations']}件")
    lines.append("")

    if results:
        lines.append("### 操作詳細")
        lines.append("")
        for r in results:
            tool = r.get("tool_name", "unknown")
            result = r.get("result", {})
            success = result.get("success", False)
            icon = "v" if success else "x"
            if success:
                lines.append(f"- [{icon}] {tool}: 成功")
            else:
                error = result.get("error", "不明なエラー")
                lines.append(f"- [{icon}] {tool}: 失敗 - {error}")

    if summary.get("errors"):
        lines.append("")
        lines.append("### エラー詳細")
        lines.append("")
        for err in summary["errors"]:
            lines.append(f"- {err}")

    return "\n".join(lines)


def _categorize_failure(errors: list[str]) -> str:
    """エラーメッセージから失敗カテゴリを推定。"""
    if not errors:
        return "unknown"
    combined = " ".join(errors).lower()
    for keyword, category in _FAILURE_CATEGORIES.items():
        if keyword in combined:
            return category
    return "api_error"

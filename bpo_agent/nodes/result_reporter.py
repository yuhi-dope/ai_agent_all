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
            arguments = r.get("arguments", {})
            success = result.get("success", False)
            icon = "v" if success else "x"

            # 操作コンテキスト（app_id, フィールド情報等）を抽出
            context = _extract_operation_context(tool, arguments)

            if success:
                lines.append(f"- [{icon}] {tool}: 成功{context}")
            else:
                error = result.get("error", "不明なエラー")
                lines.append(f"- [{icon}] {tool}: 失敗{context}")
                lines.append(f"  - エラー: {error}")

    if summary.get("errors"):
        lines.append("")
        lines.append("### エラー詳細")
        lines.append("")
        for err in summary["errors"]:
            lines.append(f"- {err}")

    return "\n".join(lines)


def _extract_operation_context(tool_name: str, arguments: dict) -> str:
    """操作の引数からレポート用のコンテキスト情報を抽出する。

    app_id、フィールドコード/名前など、エラー箇所特定に役立つ情報を返す。
    企業データ（レコード値等）は含めない。
    """
    if not arguments:
        return ""

    parts: list[str] = []

    # app_id（kintone 系共通）
    app_id = arguments.get("app_id") or arguments.get("app")
    if app_id:
        parts.append(f"app_id={app_id}")

    # フィールド情報の抽出
    fields = arguments.get("fields") or arguments.get("properties", {})
    if isinstance(fields, list):
        # [{code, label, type, ...}] 形式
        field_codes = [f.get("code") or f.get("field_code", "") for f in fields if isinstance(f, dict)]
        field_codes = [c for c in field_codes if c]
        if field_codes:
            parts.append(f"fields=[{', '.join(field_codes)}]")
    elif isinstance(fields, dict):
        # {code: {label, type, ...}} 形式
        codes = list(fields.keys())[:10]
        if codes:
            parts.append(f"fields=[{', '.join(codes)}]")

    # layout 操作のフィールド情報
    layout = arguments.get("layout")
    if isinstance(layout, list):
        layout_codes = []
        for row in layout:
            if isinstance(row, dict):
                for field in row.get("fields", []):
                    if isinstance(field, dict):
                        code = field.get("code") or field.get("field_code", "")
                        if code:
                            layout_codes.append(code)
        if layout_codes:
            parts.append(f"layout_fields=[{', '.join(layout_codes[:10])}]")

    # record_id（レコード操作時）
    record_id = arguments.get("record_id") or arguments.get("id")
    if record_id:
        parts.append(f"record_id={record_id}")

    # view_name（ビュー操作時）
    view_name = arguments.get("view_name") or arguments.get("name")
    if view_name and "view" in tool_name.lower():
        parts.append(f"view={view_name}")

    if not parts:
        return ""
    return f" ({', '.join(parts)})"


def _categorize_failure(errors: list[str]) -> str:
    """エラーメッセージから失敗カテゴリを推定。"""
    if not errors:
        return "unknown"
    combined = " ".join(errors).lower()
    for keyword, category in _FAILURE_CATEGORIES.items():
        if keyword in combined:
            return category
    return "api_error"

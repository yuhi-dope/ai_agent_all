"""Review & Guardrails: Secret Scan → Lint/Build → Unit → E2E。NG なら status=review_ng, error_logs に追加。

lint/build/test はすべて Docker サンドボックス内の MCP サーバー経由で実行する。
Secret Scan はホスト側の純 Python で実行（コンテナ起動前にブロック）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import List, Optional

from develop_agent.config import MAX_LINES_PER_PUSH, MAX_RETRY
from develop_agent.sandbox.client import SandboxMCPClient
from agent.state import AgentState
from develop_agent.utils.guardrails import (
    count_generated_code_lines,
    run_secret_scan,
)
from develop_agent.utils.guardrails_sandbox import (
    run_e2e_test_sandbox,
    run_lint_build_check_sandbox,
    run_unit_test_sandbox,
)
from agent.utils.rule_loader import load_rule

logger = logging.getLogger(__name__)


def _extract_purpose_snippet(spec_markdown: str, max_chars: int = 400) -> str:
    """spec_markdown から目的の抜粋を取得。## 目的 セクションがあればその内容、なければ概要の先頭を使用。"""
    spec = (spec_markdown or "").strip()
    if not spec:
        return ""
    if "## 目的" in spec or "## 目的 " in spec:
        start = spec.find("## 目的")
        end = spec.find("\n## ", start + 1) if start >= 0 else -1
        segment = spec[start : end] if end > start else spec[start : start + max_chars]
        return segment.strip()[:max_chars]
    for head in ("## 概要", "## 条件・手段"):
        if head in spec:
            start = spec.find(head)
            end = spec.find("\n## ", start + 1) if start >= 0 else -1
            segment = spec[start : end] if end > start else spec[start : start + max_chars]
            return segment.strip()[:max_chars]
    return spec[:max_chars]


def _write_report_html(work_dir: Path, state: AgentState) -> None:
    """この run の成果物サマリを report.html に書き出す。"""
    spec_markdown = state.get("spec_markdown") or ""
    generated_code = state.get("generated_code") or {}
    run_id = state.get("run_id") or ""
    output_subdir = state.get("output_subdir") or ""

    purpose = _extract_purpose_snippet(spec_markdown)
    purpose_escaped = purpose.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>\n")
    file_list = sorted({_normalize_rel_path(k) for k in generated_code if _normalize_rel_path(k)})

    file_rows = "".join(f"    <li><code>{p.replace('<', '&lt;')}</code></li>\n" for p in file_list)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Run Report - {run_id}</title>
</head>
<body>
  <h1>成果物サマリ</h1>
  <p><strong>Run ID:</strong> {run_id}</p>
  <p><strong>output_subdir:</strong> {output_subdir}</p>
  <h2>目的・概要</h2>
  <div>{purpose_escaped}</div>
  <h2>設計書</h2>
  <p><a href="spec.md">spec.md</a></p>
  <h2>生成ファイル一覧</h2>
  <ul>
{file_rows}  </ul>
</body>
</html>
"""
    (work_dir / "report.html").write_text(html, encoding="utf-8")


def _normalize_rel_path(rel_path: str) -> str:
    """Coder 出力のキーから先頭・末尾のバッククォートや --- を除去して正規の相対パスにする。"""
    s = rel_path.replace("\\", "/").strip()
    while s.startswith("`") or s.startswith("-") or s.startswith(" "):
        s = s.lstrip("`- ")
    while s.endswith("`") or s.endswith("-") or s.endswith(" "):
        s = s.rstrip("`- ")
    if s.startswith("/"):
        s = s[1:]
    return s


def _write_generated_code(work_dir: Path, generated_code: dict[str, str]) -> None:
    """generated_code を work_dir に書き出す。相対パスの正規化と必要なディレクトリの自動作成を行う。"""
    for rel_path, content in generated_code.items():
        rel_path = _normalize_rel_path(rel_path)
        if not rel_path:
            continue
        path = work_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _review_improvement_text(
    scan_passed: bool,
    scan_findings: list[str],
    lint_passed: bool,
    lint_findings: list[str],
    unit_passed: bool = True,
    unit_findings: Optional[List[str]] = None,
    e2e_passed: bool = True,
    e2e_findings: Optional[List[str]] = None,
    lines_ok: bool = True,
    lines_count: int = 0,
) -> str:
    """改善ルール案のテキストを組み立てる。"""
    unit_findings = unit_findings or []
    e2e_findings = e2e_findings or []
    parts = [
        "# Review フェーズ 改善・追加ルール案\n\n",
        "## 今回の結果\n",
        f"- Secret Scan: {'OK' if scan_passed else 'NG'}\n",
        f"- Lint/Build: {'OK' if lint_passed else 'NG'}\n",
        f"- Unit Test: {'OK' if unit_passed else 'NG'}\n",
        f"- E2E Test: {'OK' if e2e_passed else 'NG'}\n",
        f"- 変更量: {lines_count} 行 (上限 {MAX_LINES_PER_PUSH}) {'OK' if lines_ok else 'NG'}\n",
    ]
    if not scan_passed and scan_findings:
        parts.append("\n### Secret Scan 検出\n")
        for f in scan_findings[:5]:
            parts.append(f"- {f[:300]}\n")
    if not lint_passed and lint_findings:
        parts.append("\n### Lint/Build 検出\n")
        for f in lint_findings[:5]:
            parts.append(f"- {f[:300]}\n")
    if not unit_passed and unit_findings:
        parts.append("\n### Unit Test 検出\n")
        for f in unit_findings[:5]:
            parts.append(f"- {f[:300]}\n")
    if not e2e_passed and e2e_findings:
        parts.append("\n### E2E Test 検出\n")
        for f in e2e_findings[:5]:
            parts.append(f"- {f[:300]}\n")
    parts.append("\n## review_rules.md への追加推奨\n")
    parts.append("上記で繰り返し出るパターンがあれば、除外方針やチェック観点として追記してください。\n")
    return "".join(parts)


def review_guardrails_node(state: AgentState) -> dict:
    """Secret Scan → Lint/Build → Unit → E2E を実行。

    Secret Scan はホスト側で実行し、lint/build/test は Docker サンドボックス内で MCP 経由で実行する。
    LangGraph の _wrap_node_with_timeout (ThreadPoolExecutor) が sync を期待するため sync wrapper。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already inside an event loop (e.g. FastAPI) — run in a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(_review_guardrails_async(state))
            ).result()
    else:
        return asyncio.run(_review_guardrails_async(state))


async def _review_guardrails_async(state: AgentState) -> dict:
    """Secret Scan (ホスト) → サンドボックス内で Lint/Build/Unit/E2E を実行。"""
    generated_code = state.get("generated_code") or {}
    workspace_root = state.get("workspace_root") or "."
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = Path(workspace_root) / rules_dir_name
    load_rule(rules_dir, "review_rules", "")  # 将来の参照用に読み込みのみ

    error_logs = list(state.get("error_logs") or [])

    output_subdir = state.get("output_subdir") or f"output/{state.get('run_id', 'default')}"

    # ── 1) Secret Scan（ホスト側・コンテナ起動前）──────────────
    scan_result = run_secret_scan(generated_code)
    if not scan_result.passed:
        error_logs.append("Secret Scan FAILED: " + "; ".join(scan_result.findings[:5]))
        new_sig = hashlib.sha256(("secret:" + "|".join(scan_result.findings[:3])).encode()).hexdigest()[:16]
        out: dict = {
            "error_logs": error_logs,
            "status": "review_ng",
            "last_error_signature": new_sig,
        }
        if state.get("output_rules_improvement"):
            out["review_rules_improvement"] = _review_improvement_text(
                False, scan_result.findings, True, []
            )
        return out

    # ── 2) サンドボックス内で Lint/Build/Unit/E2E ─────────────
    sandbox_audit: list[dict] = []
    try:
        async with SandboxMCPClient() as sandbox:
            # ファイルをサンドボックスに書き込み
            await sandbox.write_generated_code(generated_code, _normalize_rel_path)
            await sandbox.write_file("spec.md", state.get("spec_markdown") or "")

            # 3) Lint / Build Check
            lint_result = await run_lint_build_check_sandbox(sandbox, generated_code)
            if not lint_result.passed:
                msgs = lint_result.findings[:5]
                error_logs.extend(msgs)
                new_sig = hashlib.sha256(("lint:" + "|".join(msgs)).encode()).hexdigest()[:16]
                sandbox_audit = await sandbox.get_audit_log()
                out = {
                    "error_logs": error_logs,
                    "status": "review_ng",
                    "last_error_signature": new_sig,
                    "sandbox_audit_log": sandbox_audit,
                }
                if state.get("output_rules_improvement"):
                    out["review_rules_improvement"] = _review_improvement_text(
                        True, [], False, lint_result.findings
                    )
                return out

            # 4) Unit Test（Lint 合格時のみ）
            unit_result = await run_unit_test_sandbox(sandbox, generated_code)
            if not unit_result.passed:
                msgs = unit_result.findings[:5]
                error_logs.extend(msgs)
                new_sig = hashlib.sha256(("unit:" + "|".join(msgs)).encode()).hexdigest()[:16]
                sandbox_audit = await sandbox.get_audit_log()
                out = {
                    "error_logs": error_logs,
                    "status": "review_ng",
                    "last_error_signature": new_sig,
                    "sandbox_audit_log": sandbox_audit,
                }
                if state.get("output_rules_improvement"):
                    out["review_rules_improvement"] = _review_improvement_text(
                        True, [], True, [], False, unit_result.findings, True, [], True, 0
                    )
                return out

            # 5) E2E Test（Unit 合格時のみ）
            e2e_result = await run_e2e_test_sandbox(sandbox, generated_code)
            if not e2e_result.passed:
                msgs = e2e_result.findings[:5]
                error_logs.extend(msgs)
                new_sig = hashlib.sha256(("e2e:" + "|".join(msgs)).encode()).hexdigest()[:16]
                sandbox_audit = await sandbox.get_audit_log()
                out = {
                    "error_logs": error_logs,
                    "status": "review_ng",
                    "last_error_signature": new_sig,
                    "sandbox_audit_log": sandbox_audit,
                }
                if state.get("output_rules_improvement"):
                    out["review_rules_improvement"] = _review_improvement_text(
                        True, [], True, [], True, [], False, e2e_result.findings, True, 0
                    )
                return out

            # 6) 変更量チェック（純 Python、ホスト側）
            lines_count = count_generated_code_lines(generated_code)
            if lines_count > MAX_LINES_PER_PUSH:
                msg = f"変更量が {MAX_LINES_PER_PUSH} 行を超えています（{lines_count} 行）。タスクを分割するか、変更範囲を縮小してください。"
                error_logs.append(msg)
                new_sig = hashlib.sha256(f"lines:{lines_count}".encode()).hexdigest()[:16]
                sandbox_audit = await sandbox.get_audit_log()
                out = {
                    "error_logs": error_logs,
                    "status": "review_ng",
                    "last_error_signature": new_sig,
                    "sandbox_audit_log": sandbox_audit,
                }
                if state.get("output_rules_improvement"):
                    out["review_rules_improvement"] = _review_improvement_text(
                        True, [], True, [], True, [], True, [], False, lines_count
                    )
                return out

            # 全チェック通過 — 監査ログ取得
            sandbox_audit = await sandbox.get_audit_log()

    except Exception as e:
        # サンドボックス起動失敗等
        logger.exception("Sandbox error: %s", e)
        error_logs.append(f"Sandbox error: {e}")
        return {
            "error_logs": error_logs,
            "status": "review_ng",
            "last_error_signature": hashlib.sha256(f"sandbox:{e}".encode()).hexdigest()[:16],
        }

    # ── 3) ホスト側にファイル書き出し（dashboard / git push 用）──
    work_dir = Path(workspace_root) / output_subdir
    work_dir.mkdir(parents=True, exist_ok=True)
    _write_generated_code(work_dir, generated_code)

    spec_path = work_dir / "spec.md"
    spec_path.write_text(state.get("spec_markdown") or "", encoding="utf-8")

    _write_report_html(work_dir, state)

    # ── 4) 成功レスポンス ─────────────────────────────────────
    out = {
        "error_logs": error_logs,
        "status": "review_ok",
        "sandbox_audit_log": sandbox_audit,
    }
    if state.get("output_rules_improvement"):
        out["review_rules_improvement"] = _review_improvement_text(
            True, [], True, [], True, [], True, [], True, lines_count
        )
    return out


def route_after_review(state: AgentState) -> str:
    """review_guardrails の次: review_ok → github_publisher / review_ng かつ retry < MAX → fix_agent / それ以外 → __end__"""
    if state.get("status") == "review_ok":
        return "github_publisher"
    if (state.get("retry_count") or 0) < MAX_RETRY:
        return "fix_agent"
    return "__end__"

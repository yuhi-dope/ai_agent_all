"""Review & Guardrails: Secret Scan → Lint/Build。NG なら status=review_ng, error_logs に追加。"""

from pathlib import Path
import hashlib

from unicorn_agent.state import AgentState
from unicorn_agent.utils.guardrails import run_secret_scan, run_lint_build_check
from unicorn_agent.config import MAX_RETRY


def _write_generated_code(work_dir: Path, generated_code: dict[str, str]) -> None:
    """generated_code を work_dir に書き出す。相対パスの正規化と必要なディレクトリの自動作成を行う。"""
    for rel_path, content in generated_code.items():
        rel_path = rel_path.replace("\\", "/").strip()
        if rel_path.startswith("/"):
            rel_path = rel_path[1:]
        if not rel_path:
            continue
        path = work_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def review_guardrails_node(state: AgentState) -> dict:
    """Secret Scan → Lint/Build を実行。OK なら review_ok、NG なら review_ng と error_logs。"""
    generated_code = state.get("generated_code") or {}
    workspace_root = state.get("workspace_root") or "."
    error_logs = list(state.get("error_logs") or [])

    work_dir = Path(workspace_root)
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) 生成コードをディスクに書き出し（Lint/Build 用）
    _write_generated_code(work_dir, generated_code)

    # 2) Secret Scan（必須）
    scan_result = run_secret_scan(generated_code)
    if not scan_result.passed:
        error_logs.append("Secret Scan FAILED: " + "; ".join(scan_result.findings[:5]))
        new_sig = hashlib.sha256(("secret:" + "|".join(scan_result.findings[:3])).encode()).hexdigest()[:16]
        return {
            "error_logs": error_logs,
            "status": "review_ng",
            "last_error_signature": new_sig,
        }

    # 3) Lint / Build Check（サンドボックス内実行前提）
    lint_result = run_lint_build_check(work_dir, generated_code)
    if not lint_result.passed:
        msgs = lint_result.findings[:5]
        error_logs.extend(msgs)
        new_sig = hashlib.sha256(("lint:" + "|".join(msgs)).encode()).hexdigest()[:16]
        return {
            "error_logs": error_logs,
            "status": "review_ng",
            "last_error_signature": new_sig,
        }

    # 同一エラー 3 回は graph の条件エッジで打ち切り。ここでは status のみ返す
    return {
        "error_logs": error_logs,
        "status": "review_ok",
    }


def route_after_review(state: AgentState) -> str:
    """review_guardrails の次: review_ok → github_publisher / review_ng かつ retry < MAX → fix_agent / それ以外 → __end__"""
    if state.get("status") == "review_ok":
        return "github_publisher"
    if (state.get("retry_count") or 0) < MAX_RETRY:
        return "fix_agent"
    return "__end__"

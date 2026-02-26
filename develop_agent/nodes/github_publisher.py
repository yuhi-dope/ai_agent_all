"""GitHub Publisher: main へ直接コミット・プッシュ。全チェック合格時のみ呼ばれる。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent.state import AgentState
from develop_agent.nodes.review_guardrails import _normalize_rel_path


def github_publisher_node(state: AgentState) -> dict:
    """workspace_root の変更を main ブランチに直接コミット・プッシュする。"""
    workspace_root = state.get("workspace_root") or "."
    generated_code = state.get("generated_code") or {}
    user_requirement = (state.get("user_requirement") or "")[:100]

    token = os.environ.get("GITHUB_TOKEN")
    repo_full = os.environ.get("GITHUB_REPOSITORY")  # e.g. owner/repo

    if not token or not repo_full:
        return {
            "status": "published",
            "error_logs": list(state.get("error_logs") or []) + [
                "GITHUB_TOKEN or GITHUB_REPOSITORY not set; skip push"
            ],
        }

    work_dir = Path(workspace_root)
    output_subdir = state.get("output_subdir") or f"output/{state.get('run_id', 'default')}"
    error_logs = list(state.get("error_logs") or [])

    if not (work_dir / ".git").exists():
        error_logs.append("workspace_root is not a git repo; cannot push")
        return {"status": "failed", "error_logs": error_logs}

    try:
        # output_subdir 配下の生成ファイルと spec.md を add（-f で .gitignore を無視）
        for rel in generated_code:
            norm_rel = _normalize_rel_path(rel)
            if not norm_rel:
                continue
            p = work_dir / output_subdir / norm_rel
            if p.exists():
                subprocess.run(
                    ["git", "add", "-f", str(p)],
                    cwd=work_dir,
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
        spec_path = work_dir / output_subdir / "spec.md"
        if spec_path.exists():
            subprocess.run(
                ["git", "add", "-f", str(spec_path)],
                cwd=work_dir,
                check=True,
                capture_output=True,
                timeout=10,
            )
        report_path = work_dir / output_subdir / "report.html"
        if report_path.exists():
            subprocess.run(
                ["git", "add", "-f", str(report_path)],
                cwd=work_dir,
                check=True,
                capture_output=True,
                timeout=10,
            )
        subprocess.run(
            ["git", "commit", "-m", f"Agent: {user_requirement[:72]}"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # main へ直接プッシュ（HTTPS + token）
        remote_url = f"https://{token}@github.com/{repo_full}.git"
        subprocess.run(
            ["git", "push", remote_url, "HEAD:main"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.CalledProcessError as e:
        error_logs.append(f"git error: {e.stderr or e.stdout or str(e)}")
        return {"status": "failed", "error_logs": error_logs}
    except Exception as e:
        error_logs.append(str(e))
        return {"status": "failed", "error_logs": error_logs}

    return {
        "status": "published",
        "error_logs": error_logs,
        "sandbox_audit_log": state.get("sandbox_audit_log") or [],
    }

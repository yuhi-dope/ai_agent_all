"""GitHub Publisher: ブランチ作成・PR 作成。全チェック合格時のみ呼ばれる。"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from develop_agent.state import AgentState
from develop_agent.utils.rule_loader import load_rule

from develop_agent.nodes.review_guardrails import _normalize_rel_path


def _parse_pr_rules(pr_rules_text: str) -> tuple[str | None, str | None]:
    """pr_rules.md から title と body をパース。見つからなければ (None, None)。"""
    title: str | None = None
    body: str | None = None
    lines = pr_rules_text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            i += 1
            continue
        if line.strip().lower().startswith("body:"):
            rest = [lines[j] for j in range(i + 1, len(lines))]
            body_lines = []
            for L in rest:
                if L.strip() == "---":
                    break
                body_lines.append(L)
            body = "\n".join(body_lines).strip() if body_lines else ""
            break
        i += 1
    return (title, body)


def _sanitize_branch_name(name: str) -> str:
    """ブランチ名に使えるようにサニタイズ。"""
    s = re.sub(r"[^a-zA-Z0-9/_.-]", "-", name)[:80]
    return s.strip("-") or "agent-patch"


def github_publisher_node(state: AgentState) -> dict:
    """workspace_root の変更をブランチにコミット・プッシュし、PR を作成する。"""
    workspace_root = state.get("workspace_root") or "."
    generated_code = state.get("generated_code") or {}
    user_requirement = (state.get("user_requirement") or "")[:100]

    token = os.environ.get("GITHUB_TOKEN")
    repo_full = os.environ.get("GITHUB_REPOSITORY")  # e.g. owner/repo

    if not token or not repo_full:
        return {
            "status": "published",
            "pr_url": "",
            "error_logs": list(state.get("error_logs") or []) + [
                "GITHUB_TOKEN or GITHUB_REPOSITORY not set; skip PR creation"
            ],
        }

    work_dir = Path(workspace_root)
    output_subdir = state.get("output_subdir") or f"output/{state.get('run_id', 'default')}"
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = work_dir / rules_dir_name
    pr_rules_text = load_rule(rules_dir, "pr_rules", "")
    pr_title_override, pr_body_override = _parse_pr_rules(pr_rules_text)

    branch_name = "agent/" + _sanitize_branch_name(user_requirement or "patch")
    error_logs = list(state.get("error_logs") or [])

    # 1) ローカルで git ブランチ作成・コミット・プッシュ（サンドボックス内実行前提）
    if (work_dir / ".git").exists():
        try:
            subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=work_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
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
            # remote に push（HTTPS + token）
            remote_url = f"https://{token}@github.com/{repo_full}.git"
            subprocess.run(
                ["git", "push", remote_url, f"HEAD:{branch_name}"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.CalledProcessError as e:
            error_logs.append(f"git error: {e.stderr or e.stdout or str(e)}")
            return {"status": "failed", "pr_url": "", "error_logs": error_logs}
        except Exception as e:
            error_logs.append(str(e))
            return {"status": "failed", "pr_url": "", "error_logs": error_logs}
    else:
        error_logs.append("workspace_root is not a git repo; cannot push")
        return {"status": "failed", "pr_url": "", "error_logs": error_logs}

    # 2) PR 作成（PyGithub）
    title = pr_title_override if pr_title_override else f"Agent: {user_requirement[:80]}"
    body = pr_body_override if pr_body_override else "Auto-generated by Develop Agent. Please review before merge."
    try:
        from github import Github

        gh = Github(token)
        repo = gh.get_repo(repo_full)
        pr = repo.create_pull(
            title=title,
            body=body,
            head=branch_name,
            base=repo.default_branch,
        )
        pr_url = pr.html_url or ""
    except ImportError:
        pr_url = f"https://github.com/{repo_full}/compare/{branch_name}"
        error_logs.append("PyGithub not installed; open URL above to create PR manually")
    except Exception as e:
        error_logs.append(f"create_pull: {e}")
        pr_url = f"https://github.com/{repo_full}/compare/{branch_name}"

    out: dict = {
        "status": "published",
        "pr_url": pr_url,
    }
    if state.get("output_rules_improvement"):
        out["pr_rules_improvement"] = (
            f"# PR フェーズ 改善・追加ルール案\n\n"
            f"## 今回の実行\n"
            f"- ブランチ: {branch_name}\n"
            f"- PR URL: {pr_url}\n\n"
            f"## pr_rules.md への追加推奨\n"
            f"定型の title/body テンプレートを使う場合は、title: / body: を pr_rules.md に記載してください。\n"
        )
    return out

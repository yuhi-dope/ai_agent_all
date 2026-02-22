"""
Phase 2.1: HTTP でエージェントを起動する FastAPI サーバー。
POST /run で requirement または notion_page_id を受け取り、invoke を実行して結果を JSON で返す。
"""

import hmac
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# プロジェクトルートを path に追加（develop_agent の import 前に必要）
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env.local")

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from notion_client.errors import HTTPResponseError  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from develop_agent import initial_state  # noqa: E402
from develop_agent.graph import invoke, invoke_spec, invoke_impl  # noqa: E402
from server import cost as server_cost  # noqa: E402
from server import notion_client  # noqa: E402
from server import next_system_suggestor  # noqa: E402
from server import persist  # noqa: E402
from server import rules_merge  # noqa: E402
from server import settings as server_settings  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Develop Agent API", version="0.1.0")


class RunRequest(BaseModel):
    requirement: Optional[str] = None
    notion_page_id: Optional[str] = None
    workspace_root: str = "."
    rules_dir: str = "rules"
    output_rules_improvement: bool = False
    skip_accumulation_inject: bool = True
    genre: Optional[str] = None


class RunResponse(BaseModel):
    status: str
    pr_url: str = ""
    run_id: str = ""
    output_subdir: str = ""
    error_logs: List[str] = []
    spec_markdown_preview: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: Optional[float] = None
    budget_exceeded: bool = False
    genre: str = ""
    genre_override_reason: str = ""


class ImplementResponse(BaseModel):
    status: str
    pr_url: str = ""
    run_id: str = ""
    error_logs: List[str] = []
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: Optional[float] = None


class SettingsResponse(BaseModel):
    auto_execute: bool


class SettingsUpdateRequest(BaseModel):
    auto_execute: bool


class RunFromDatabaseRequest(BaseModel):
    notion_database_id: str
    workspace_root: str = "."
    rules_dir: str = "rules"
    output_rules_improvement: bool = False


class RunFromDatabaseResponse(BaseModel):
    processed: int
    results: List[dict]
    message: str = ""


@app.get("/health")
def health():
    """生存確認用。"""
    return {"status": "ok"}


@app.get("/api/runs")
def api_runs(limit: int = 50):
    """run 一覧を返す。Supabase 未設定時は空リスト。"""
    return {"runs": persist.get_runs(limit=limit)}


@app.get("/api/features")
def api_features(run_id: Optional[str] = None, limit: int = 100):
    """features を返す。run_id 指定時はその run の feature のみ。"""
    return {"features": persist.get_features(run_id=run_id, limit=limit)}


@app.get("/api/settings", response_model=SettingsResponse)
def api_get_settings():
    """現在の設定を返す。"""
    return SettingsResponse(auto_execute=server_settings.get_auto_execute())


@app.put("/api/settings", response_model=SettingsResponse)
def api_update_settings(body: SettingsUpdateRequest):
    """設定を更新する。"""
    server_settings.set_auto_execute(body.auto_execute)
    return SettingsResponse(auto_execute=body.auto_execute)


@app.get("/api/runs/{run_id}/spec")
def api_run_spec(run_id: str):
    """run_id の spec_markdown 全文を返す。"""
    run = persist.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"spec_markdown": run.get("spec_markdown") or ""}


@app.post("/run/{run_id}/implement", response_model=ImplementResponse)
def run_implement(run_id: str):
    """spec_review 状態の run を再開し、実装フェーズを実行する。"""
    snapshot = persist.load_state_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run {run_id} が見つからないか、spec_review 状態ではありません",
        )

    persist.update_run_status(run_id, {"status": "coding", "state_snapshot": None})

    try:
        result = invoke_impl(snapshot)
    except Exception as e:
        persist.update_run_status(run_id, {"status": "failed"})
        raise HTTPException(status_code=500, detail=str(e))

    final_status = result.get("status") or ""
    pr_url = result.get("pr_url") or ""
    output_subdir = result.get("output_subdir") or ""

    persist.update_run_status(run_id, {
        "status": final_status,
        "retry_count": result.get("retry_count") or 0,
        "pr_url": pr_url or None,
        "state_snapshot": None,
    })

    try:
        persist.persist_features(run_id, result)
    except Exception:
        pass

    # Sandbox 監査ログを Supabase に保存
    try:
        audit_log = result.get("sandbox_audit_log") or []
        if audit_log:
            persist.persist_audit_logs(run_id, audit_log)
    except Exception:
        pass

    workspace_root = Path(result.get("workspace_root") or ".")
    rules_dir = result.get("rules_dir") or "rules"
    if result.get("output_rules_improvement") and output_subdir:
        _write_rules_suggestions(workspace_root, output_subdir, run_id, result)
    if result.get("output_rules_improvement") and output_subdir and final_status == "published":
        rules_merge.merge_improvements_into_rules(
            workspace_root=workspace_root,
            rules_dir_name=rules_dir,
            run_id=run_id,
            result=result,
            genre=result.get("genre"),
        )

    notion_page_id = result.get("notion_page_id") or snapshot.get("notion_page_id") or ""
    if notion_page_id:
        try:
            notion_client.update_page_status(
                notion_page_id, "完了済", run_id=run_id, pr_url=pr_url or None
            )
        except Exception:
            pass

    total_in = result.get("total_input_tokens") or 0
    total_out = result.get("total_output_tokens") or 0
    cost_usd, _ = server_cost.check_budget(total_in, total_out)

    return ImplementResponse(
        status=final_status,
        pr_url=pr_url,
        run_id=run_id,
        error_logs=result.get("error_logs") or [],
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        cost_usd=round(cost_usd, 6),
    )


_DASHBOARD_HTML = _project_root / "server" / "static" / "dashboard" / "index.html"


@app.get("/dashboard")
def dashboard():
    """簡易 UI: run 一覧・詳細・次に作るシステムの提案を表示する 1 ページ。"""
    if _DASHBOARD_HTML.exists():
        return FileResponse(_DASHBOARD_HTML)
    raise HTTPException(status_code=404, detail="Dashboard not found")


@app.get("/api/next-system-suggestion")
def api_next_system_suggestion():
    """data/next_system_suggestion.md の内容と更新日時を返す。ファイルがなければ 404。"""
    path = _project_root / "data" / "next_system_suggestion.md"
    if not path.exists():
        return {"content": None, "updated_at": None}
    text = path.read_text(encoding="utf-8")
    first_line = text.split("\n")[0]
    updated_at = None
    if first_line.startswith("最終更新:"):
        updated_at = first_line.replace("最終更新:", "").strip()
        content = "\n".join(text.split("\n")[2:]).strip()
    else:
        content = text
    return {"content": content, "updated_at": updated_at}


def _write_rules_suggestions(
    workspace_root: Path,
    output_subdir: str,
    run_id: str,
    result: dict,
) -> None:
    """output_rules_improvement=True のとき、output/<開発名>/rules_suggestions.md を書き出す。"""
    dir_path = workspace_root / output_subdir
    dir_path.mkdir(parents=True, exist_ok=True)
    name = output_subdir.replace("output/", "").strip() or run_id

    def section(title: str, key: str) -> str:
        text = (result.get(key) or "").strip()
        body = text if text else "（今回の run では提案なし）"
        return f"## {title}\n\n### 今回の提案\n\n{body}\n\n---\n\n"

    body_parts = [
        f"# ルール改善案（Run ID: {run_id} / 開発名: {name})\n\n",
        "採用する案は、該当する rules/*.md の末尾に手動で追記してください。\n\n---\n\n",
        section("Spec（spec_rules.md）", "spec_rules_improvement"),
        section("Coder（coder_rules.md）", "coder_rules_improvement"),
        section("Review（review_rules.md）", "review_rules_improvement"),
        section("Fix（fix_rules.md）", "fix_rules_improvement"),
        section("PR（pr_rules.md）", "pr_rules_improvement"),
    ]
    (dir_path / "rules_suggestions.md").write_text("".join(body_parts), encoding="utf-8")


def _resolve_user_requirement(body: RunRequest) -> str:
    """
    requirement を最優先。なければ notion_page_id + NOTION_API_KEY で Notion から取得。
    どちらもない、または notion のみで KEY なしの場合は HTTPException(400) を投げる。
    """
    if body.requirement is not None and body.requirement.strip():
        return body.requirement.strip()

    if body.notion_page_id and body.notion_page_id.strip():
        api_key = os.environ.get("NOTION_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="requirement または notion_page_id（要 NOTION_API_KEY）を指定してください",
            )
        try:
            content = notion_client.fetch_page_content(body.notion_page_id.strip())
        except HTTPResponseError as e:
            if e.status in (403, 404):
                logger.warning(
                    "Notion page could not be retrieved: status=%s code=%s body=%s",
                    e.status,
                    getattr(e, "code", None),
                    getattr(e, "body", ""),
                )
                raise HTTPException(
                    status_code=400,
                    detail="Notion page could not be retrieved (not found or no access). Confirm the page ID and that the page is connected to the integration.",
                ) from e
            raise
        if not content.strip():
            raise HTTPException(status_code=400, detail="ページが空です")
        return content.strip()

    raise HTTPException(
        status_code=400,
        detail="requirement または notion_page_id（要 NOTION_API_KEY）を指定してください",
    )


@app.post("/run", response_model=RunResponse)
def run_agent(body: RunRequest):
    """
    要件を受け取り、エージェントを実行する。
    auto_execute ON: Spec → Coder → Review → PR まで一気通貫。
    auto_execute OFF: Spec のみ実行し spec_review 状態で返す（ダッシュボードで確認後に /run/{run_id}/implement で再開）。
    """
    user_requirement = _resolve_user_requirement(body)
    nin_suggestor, nout_suggestor = 0, 0
    if user_requirement:
        try:
            suggestion, nin_suggestor, nout_suggestor = next_system_suggestor.generate_and_save(
                Path(body.workspace_root),
            )
            if suggestion and not body.skip_accumulation_inject:
                user_requirement = f"{suggestion}\n\n---\n\n{user_requirement}"
        except Exception:
            pass

    auto_execute = server_settings.get_auto_execute()

    try:
        state = initial_state(
            user_requirement=user_requirement,
            workspace_root=body.workspace_root,
            rules_dir=body.rules_dir,
            output_rules_improvement=body.output_rules_improvement,
            genre=body.genre,
            notion_page_id=body.notion_page_id,
        )

        if auto_execute:
            result = invoke(state)
        else:
            result = invoke_spec(state)
            result["status"] = "spec_review"

            try:
                persist.persist_spec_snapshot(result)
            except Exception:
                pass

            spec_preview = (result.get("spec_markdown") or "")[:500]
            total_in = (result.get("total_input_tokens") or 0) + nin_suggestor
            total_out = (result.get("total_output_tokens") or 0) + nout_suggestor
            cost_usd, budget_exceeded = server_cost.check_budget(total_in, total_out)
            return RunResponse(
                status="spec_review",
                pr_url="",
                run_id=result.get("run_id", ""),
                output_subdir=result.get("output_subdir", ""),
                error_logs=result.get("error_logs") or [],
                spec_markdown_preview=spec_preview,
                total_input_tokens=total_in,
                total_output_tokens=total_out,
                cost_usd=round(cost_usd, 6),
                budget_exceeded=budget_exceeded,
                genre=result.get("genre") or "",
                genre_override_reason=result.get("genre_override_reason") or "",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    spec_preview = ""
    if result.get("spec_markdown"):
        spec_preview = (result["spec_markdown"] or "")[:500]

    output_subdir = result.get("output_subdir") or ""

    try:
        persist.persist_run(
            workspace_root=Path(body.workspace_root),
            output_subdir=output_subdir,
            result=result,
        )
    except Exception:
        pass

    # Sandbox 監査ログを Supabase に保存
    try:
        audit_log = result.get("sandbox_audit_log") or []
        if audit_log:
            persist.persist_audit_logs(result.get("run_id", ""), audit_log)
    except Exception:
        pass

    if body.output_rules_improvement and output_subdir:
        _write_rules_suggestions(
            workspace_root=Path(body.workspace_root),
            output_subdir=output_subdir,
            run_id=result.get("run_id") or "",
            result=result,
        )
    if (
        body.output_rules_improvement
        and output_subdir
        and result.get("status") == "published"
    ):
        rules_merge.merge_improvements_into_rules(
            workspace_root=Path(body.workspace_root),
            rules_dir_name=body.rules_dir,
            run_id=result.get("run_id") or "",
            result=result,
            genre=body.genre or result.get("genre"),
        )

    total_in = (result.get("total_input_tokens") or 0) + nin_suggestor
    total_out = (result.get("total_output_tokens") or 0) + nout_suggestor
    cost_usd, budget_exceeded = server_cost.check_budget(total_in, total_out)
    logger.info(
        "run_id=%s total_input_tokens=%s total_output_tokens=%s estimated_cost_usd=%.4f budget_exceeded=%s",
        result.get("run_id"),
        total_in,
        total_out,
        cost_usd,
        budget_exceeded,
    )
    if budget_exceeded:
        logger.warning("Run %s exceeded budget: cost_usd=%.4f", result.get("run_id"), cost_usd)

    return RunResponse(
        status=result.get("status", ""),
        pr_url=result.get("pr_url", ""),
        run_id=result.get("run_id", ""),
        output_subdir=output_subdir,
        error_logs=result.get("error_logs") or [],
        spec_markdown_preview=spec_preview,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        cost_usd=round(cost_usd, 6),
        budget_exceeded=budget_exceeded,
        genre=result.get("genre") or "",
        genre_override_reason=result.get("genre_override_reason") or "",
    )


def _verify_notion_signature(raw_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Notion Webhook の X-Notion-Signature を検証する。body は minified JSON で再計算。"""
    if not secret or not signature_header:
        return False
    try:
        body_str = raw_body.decode("utf-8")
        payload = json.loads(body_str)
        # Notion は minified JSON で署名する
        body_minified = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            body_minified.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)
    except Exception:
        return False


# 前提・用語の「実装希望」ステータス値（develop_agent.md と一致させる）
_NOTION_STATUS_READY = "実装希望"


def _run_agent_for_webhook(notion_page_id: str, genre: Optional[str]) -> None:
    """Webhook 用: notion_page_id で run を実行（バックグラウンドで呼ぶ）。"""
    try:
        run_body = RunRequest(
            notion_page_id=notion_page_id,
            workspace_root=".",
            rules_dir="rules",
            output_rules_improvement=False,
            skip_accumulation_inject=True,
            genre=genre,
        )
        run_agent(run_body)
    except Exception as e:
        logger.exception("Webhook background run failed for page_id=%s: %s", notion_page_id, e)


@app.post("/webhook/notion")
async def webhook_notion(request: Request, background_tasks: BackgroundTasks):
    """
    Notion Webhook 受信。検証リクエスト（verification_token のみ）には 200 を返す。
    イベント時は X-Notion-Signature を検証し、entity.type が page かつステータスが「実装希望」の場合に run をバックグラウンドで起動する。
    """
    raw_body = await request.body()
    signature = request.headers.get("X-Notion-Signature") or ""

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 検証リクエスト: body に verification_token のみ
    if set(payload.keys()) <= {"verification_token"} and "verification_token" in payload:
        return {"verification_token": payload["verification_token"]}

    secret = (os.environ.get("NOTION_WEBHOOK_SECRET") or "").strip()
    if not _verify_notion_signature(raw_body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = payload.get("type") or ""
    entity = payload.get("entity") or {}
    entity_id = (entity.get("id") or "").strip()
    entity_type = (entity.get("type") or "").strip()

    if entity_type != "page" or not entity_id:
        return {"status": "ignored", "reason": "not a page event or missing entity.id"}

    page_events = (
        "page.content_updated",
        "page.properties_updated",
        "page.created",
    )
    if event_type not in page_events:
        return {"status": "ignored", "reason": f"event type {event_type} not triggered"}

    properties = notion_client.get_page_properties(entity_id)
    if not properties:
        return {"status": "ignored", "reason": "page not found or no access"}

    status_value = notion_client.get_select_property(properties, "ステータス")
    if status_value != _NOTION_STATUS_READY:
        return {"status": "ignored", "reason": f"status is {status_value!r}, not {_NOTION_STATUS_READY!r}"}

    genre = notion_client.get_select_property(properties, "ジャンル")
    background_tasks.add_task(_run_agent_for_webhook, entity_id, genre)
    return {"status": "accepted", "page_id": entity_id}


@app.post("/run-from-database", response_model=RunFromDatabaseResponse)
def run_from_database(body: RunFromDatabaseRequest):
    """
    Notion データベースの「実装希望」ステータスのページを取得し、順次 develop_agent で処理する。
    各ページの要件を取得 → ステータスを「実装中」に更新 → invoke → 完了後に「完了済」と run_id・PR URL を書き戻す。
    """
    try:
        pages = notion_client.query_pages_by_status(
            body.notion_database_id,
            status_value="実装希望",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPResponseError as e:
        if e.status in (403, 404):
            raise HTTPException(
                status_code=400,
                detail="Notion database could not be accessed. Confirm the database ID and integration access.",
            ) from e
        raise HTTPException(status_code=502, detail=str(e))

    if not pages:
        return RunFromDatabaseResponse(
            processed=0,
            results=[],
            message="実装希望のページがありません",
        )

    results: List[dict] = []
    workspace_root = Path(body.workspace_root)

    for page in pages:
        page_id = page.get("id") or ""
        properties = page.get("properties") or {}
        try:
            requirement = notion_client.get_requirement_from_page(page_id, properties)
        except Exception as e:
            results.append(
                {
                    "page_id": page_id,
                    "status": "error",
                    "error": f"要件の取得に失敗: {e}",
                }
            )
            continue

        try:
            notion_client.update_page_status(page_id, "実装中")
        except Exception as e:
            results.append(
                {
                    "page_id": page_id,
                    "status": "error",
                    "error": f"ステータス更新に失敗: {e}",
                }
            )
            continue

        run_id = ""
        pr_url = ""
        status = "failed"
        genre = notion_client.get_select_property(properties, "ジャンル")
        try:
            state = initial_state(
                user_requirement=requirement,
                workspace_root=body.workspace_root,
                rules_dir=body.rules_dir,
                output_rules_improvement=body.output_rules_improvement,
                genre=genre,
            )
            result = invoke(state)
            status = result.get("status", "")
            run_id = result.get("run_id") or ""
            pr_url = result.get("pr_url") or ""
            output_subdir = result.get("output_subdir") or ""
            try:
                persist.persist_run(workspace_root, output_subdir, result)
            except Exception:
                pass
            # Sandbox 監査ログを Supabase に保存
            try:
                audit_log = result.get("sandbox_audit_log") or []
                if audit_log:
                    persist.persist_audit_logs(run_id, audit_log)
            except Exception:
                pass
            if body.output_rules_improvement and output_subdir:
                _write_rules_suggestions(
                    workspace_root=workspace_root,
                    output_subdir=output_subdir,
                    run_id=run_id,
                    result=result,
                )
            if (
                body.output_rules_improvement
                and output_subdir
                and result.get("status") == "published"
            ):
                rules_merge.merge_improvements_into_rules(
                    workspace_root=workspace_root,
                    rules_dir_name=body.rules_dir,
                    run_id=run_id,
                    result=result,
                    genre=genre,
                )
        except Exception as e:
            status = "error"
            run_id = f"error: {str(e)[:200]}"

        try:
            notion_client.update_page_status(
                page_id,
                "完了済",
                run_id=run_id,
                pr_url=pr_url or None,
            )
        except Exception:
            pass

        results.append(
            {
                "page_id": page_id,
                "status": status,
                "run_id": run_id,
                "pr_url": pr_url,
            }
        )

    return RunFromDatabaseResponse(
        processed=len(results),
        results=results,
        message=f"{len(results)} 件処理しました",
    )

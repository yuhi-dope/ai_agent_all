"""
Phase 2.1: HTTP でエージェントを起動する FastAPI サーバー。
POST /run で requirement または notion_page_id を受け取り、invoke を実行して結果を JSON で返す。
"""

import hmac
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# プロジェクトルートを path に追加（develop_agent の import 前に必要）
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_project_root / ".env.local")

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request  # noqa: E402
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
from server import output_cleanup  # noqa: E402
from server import alert as server_alert  # noqa: E402
from server.auth import get_admin_user, get_current_user  # noqa: E402
from server import company as company_module  # noqa: E402
from server import oauth_store  # noqa: E402
from server import onboarding_provisioner  # noqa: E402
from server.channel_adapter import ChannelAdapter, ChannelMessage  # noqa: E402
from server.i18n import resolve_lang, translate_openapi_schema  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Startup
    await output_cleanup.start_scheduler()
    from server import token_refresh
    await token_refresh.start()
    yield
    # Shutdown
    await token_refresh.stop()


app = FastAPI(
    title="Develop Agent API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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


class CompanyCreateRequest(BaseModel):
    name: str
    employee_count: Optional[str] = None
    annual_revenue: Optional[str] = None
    industry: Optional[str] = None
    founded_date: Optional[str] = None
    corporate_number: Optional[str] = None
    password: Optional[str] = None


class CompanyJoinRequest(BaseModel):
    slug: str


class CompanyProfileRequest(BaseModel):
    employee_count: Optional[str] = None
    annual_revenue: Optional[str] = None
    industry: Optional[str] = None
    founded_date: Optional[str] = None


class OnboardingUpdateRequest(BaseModel):
    steps: dict  # e.g. {"github_repo": true, "vercel_project": true}


class CompanyLoginRequest(BaseModel):
    slug: str
    password: str


class InviteRequest(BaseModel):
    role: str = "member"


class InviteConsumeRequest(BaseModel):
    token: str


class TokenInput(BaseModel):
    access_token: str
    expires_at: str = ""


class VercelProvisionRequest(BaseModel):
    access_token: str
    supabase_anon_key: str = ""


# =====================================================================
# ランディングページ + 多言語対応 OpenAPI / Docs / ReDoc
# =====================================================================


@app.get("/", include_in_schema=False)
def landing_page():
    """ルートページ: ダッシュボード（登録/ログイン画面）にリダイレクト。"""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard")


@app.get("/openapi.json", include_in_schema=False)
def custom_openapi(lang: str = "ja"):
    """言語パラメータ付き OpenAPI スキーマを返す。"""
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    return translate_openapi_schema(schema, resolve_lang(lang))


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui(lang: str = "ja"):
    """言語切替付きカスタム Swagger UI。"""
    from fastapi.responses import HTMLResponse

    lang = resolve_lang(lang)
    other_lang = "en" if lang == "ja" else "ja"
    other_label = "English" if lang == "ja" else "日本語"
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Develop Agent API - Docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body {{ margin: 0; }}
    .lang-switcher {{
      position: fixed; top: 8px; right: 16px; z-index: 9999;
      display: flex; gap: 8px; align-items: center;
      font-family: sans-serif; font-size: 14px;
    }}
    .lang-switcher a {{
      padding: 4px 12px; border-radius: 4px; text-decoration: none;
      border: 1px solid #89bf04; color: #89bf04;
    }}
    .lang-switcher a:hover {{ background: #89bf04; color: #fff; }}
    .lang-switcher span {{ color: #fff; font-weight: bold; background: #89bf04; padding: 4px 12px; border-radius: 4px; }}
  </style>
</head>
<body>
  <div class="lang-switcher">
    <span>{lang.upper()}</span>
    <a href="/docs?lang={other_lang}">{other_label}</a>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{
      url: "/openapi.json?lang={lang}",
      dom_id: "#swagger-ui",
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: "BaseLayout",
    }});
  </script>
</body>
</html>""")


@app.get("/health")
def health():
    """生存確認用。"""
    return {"status": "ok"}


@app.get("/api/auth/config")
def api_auth_config():
    """フロントエンド向け: 認証設定（公開情報のみ）を返す。"""
    val = os.environ.get("REQUIRE_AUTH", "true").strip().lower()
    return {
        "require_auth": val not in ("false", "0", "no", ""),
        "supabase_url": os.environ.get("SUPABASE_URL", ""),
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
    }


@app.get("/api/runs")
def api_runs(limit: int = 50, user=Depends(get_current_user)):
    """run 一覧を返す。Supabase 未設定時は空リスト。company_id でフィルタ。"""
    company_id = user.get("company_id") if user else None
    return {"runs": persist.get_runs(limit=limit, company_id=company_id)}


@app.get("/api/features")
def api_features(run_id: Optional[str] = None, limit: int = 100, user=Depends(get_current_user)):
    """features を返す。run_id 指定時はその run の feature のみ。company_id でフィルタ。"""
    company_id = user.get("company_id") if user else None
    return {"features": persist.get_features(run_id=run_id, company_id=company_id, limit=limit)}


# ===== Company (テナント) =====


@app.get("/api/company/me")
def api_company_me(user=Depends(get_current_user)):
    """自分の会社情報を返す。未登録なら company=null。"""
    if not user or not user.get("company_id"):
        return {"company": None}
    company = company_module.get_company_by_id(user["company_id"])
    return {"company": company}


@app.post("/api/company/create")
def api_company_create(body: CompanyCreateRequest, user=Depends(get_current_user)):
    """会社を作成し、自分を admin として紐付ける。slug はサーバー側で自動生成。"""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    company = company_module.create_company(
        name=body.name.strip(),
        employee_count=(body.employee_count or "").strip(),
        annual_revenue=(body.annual_revenue or "").strip(),
        industry=(body.industry or "").strip(),
        founded_date=(body.founded_date or "").strip(),
        corporate_number=(body.corporate_number or "").strip(),
        password=(body.password or "").strip(),
    )
    if not company:
        raise HTTPException(status_code=500, detail="Failed to create company")
    # anonymous (no-auth mode) の場合は user_companies 紐付けをスキップ
    if user and user.get("id") != "anonymous":
        company_module.add_user_to_company(
            user_id=str(user["id"]), company_id=company["id"], role="admin"
        )
    return {"company": company}


@app.post("/api/company/join")
def api_company_join(body: CompanyJoinRequest, user=Depends(get_current_user)):
    """slug で既存の会社に参加する。"""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    slug = body.slug.strip().lower()
    company = company_module.get_company_by_slug(slug)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    # anonymous (no-auth mode) の場合は user_companies 紐付けをスキップ
    if user and user.get("id") != "anonymous":
        ok = company_module.add_user_to_company(
            user_id=str(user["id"]), company_id=company["id"], role="member"
        )
        if not ok:
            raise HTTPException(status_code=409, detail="Already a member or failed to join")
    return {"company": company}


@app.post("/api/company/login")
def api_company_login(body: CompanyLoginRequest):
    """会社IDとパスワードでログインする。認証不要。"""
    slug = body.slug.strip().lower()
    password = body.password
    if not slug or not password:
        raise HTTPException(status_code=400, detail="slug and password are required")
    company = company_module.login_company(slug, password)
    if not company:
        raise HTTPException(status_code=401, detail="Invalid company ID or password")
    return {"company": company}


@app.get("/api/company/search")
def api_company_search(q: str = "", limit: int = 10):
    """会社名/slug の部分一致検索（オートコンプリート用）。認証不要。"""
    results = company_module.search_companies(q, limit=min(limit, 20))
    return {"companies": results}


@app.get("/api/company/nta-search")
def api_nta_search(name: str = "", count: int = 8):
    """国税庁 法人番号システム Web-API を使った会社名検索プロキシ。レスポンスは XML。"""
    import httpx
    import xml.etree.ElementTree as ET

    q = name.strip()
    if len(q) < 2:
        return {"corporations": []}
    nta_app_id = os.environ.get("NTA_APP_ID", "K5JtSSk4pdbHk")
    url = "https://api.houjin-bangou.nta.go.jp/4/name"
    params = {
        "id": nta_app_id,
        "name": q,
        "type": "12",
        "mode": "2",
        "count": str(min(count, 10)),
    }
    try:
        resp = httpx.get(url, params=params, timeout=5.0)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        results = []
        for corp in root.findall("corporation"):
            results.append({
                "corporate_number": (corp.findtext("corporateNumber") or "").strip(),
                "name": (corp.findtext("name") or "").strip(),
                "prefecture": (corp.findtext("prefectureName") or "").strip(),
                "city": (corp.findtext("cityName") or "").strip(),
                "street": (corp.findtext("streetNumber") or "").strip(),
                "post_code": (corp.findtext("postCode") or "").strip(),
            })
        return {"corporations": results}
    except Exception as e:
        logger.warning("NTA API error: %s", e)
        return {"corporations": []}


@app.get("/api/company/profile")
def api_get_company_profile(user=Depends(get_current_user)):
    """自分の会社のプロフィール情報（従業員数・売上・業種）を返す。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        return {"employee_count": None, "annual_revenue": None, "industry": None, "founded_date": None}
    company = company_module.get_company_by_id(company_id)
    if not company:
        return {"employee_count": None, "annual_revenue": None, "industry": None, "founded_date": None}
    return {
        "employee_count": company.get("employee_count"),
        "annual_revenue": company.get("annual_revenue"),
        "industry": company.get("industry"),
        "founded_date": company.get("founded_date"),
    }


@app.put("/api/company/profile")
def api_update_company_profile(body: CompanyProfileRequest, user=Depends(get_current_user)):
    """自分の会社のプロフィール情報を更新する。3項目すべて入力済みなら onboarding の company_profile を自動完了。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    result = company_module.update_company_profile(
        company_id,
        employee_count=(body.employee_count or "").strip(),
        annual_revenue=(body.annual_revenue or "").strip(),
        industry=(body.industry or "").strip(),
        founded_date=(body.founded_date or "").strip(),
    )
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to update profile")
    return {
        "employee_count": result.get("employee_count"),
        "annual_revenue": result.get("annual_revenue"),
        "industry": result.get("industry"),
        "founded_date": result.get("founded_date"),
    }


@app.get("/api/company/infra")
def api_get_company_infra(user=Depends(get_current_user)):
    """自分の会社のインフラ設定（GitHub repo, Supabase URL 等）を返す。読み取り専用。書き込みは CLI (onboarding.sh) のみ。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        return {"github_repository": None, "github_token_secret_name": None, "client_supabase_url": None, "vercel_project_url": None}
    return company_module.get_company_infra(company_id)


@app.get("/api/company/onboarding")
def api_get_onboarding(user=Depends(get_current_user)):
    """自分の会社のオンボーディングステータスを返す。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        return {"steps": {}, "all_done": False}
    steps = company_module.get_onboarding(company_id)
    return {"steps": steps, "all_done": all(steps.values())}


@app.put("/api/company/onboarding")
def api_update_onboarding(body: OnboardingUpdateRequest, user=Depends(get_current_user)):
    """自分の会社のオンボーディングステータスを更新する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    result = company_module.update_onboarding(company_id, body.steps)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to update onboarding")
    return {"steps": result, "all_done": all(result.values())}


# -- 招待トークン + メンバー管理 --

@app.post("/api/company/invite")
def api_create_invite(body: InviteRequest, user=Depends(get_current_user)):
    """招待リンク用のワンタイムトークンを生成する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    result = company_module.generate_invite_token(
        company_id=company_id,
        created_by=str((user or {}).get("id", "")),
        role=body.role,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to generate invite")
    return result


@app.post("/api/company/join-by-invite")
def api_join_by_invite(body: InviteConsumeRequest, user=Depends(get_current_user)):
    """招待トークンで会社に参加する。"""
    user_id = str((user or {}).get("id", ""))
    company = company_module.consume_invite_token(body.token, user_id)
    if not company:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")
    return {"company": company}


@app.get("/api/company/members")
def api_company_members(user=Depends(get_current_user)):
    """自分の会社のメンバー一覧を返す。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        return {"members": []}
    return {"members": company_module.get_company_members(company_id)}


@app.delete("/api/company/members/{target_user_id}")
def api_remove_member(target_user_id: str, user=Depends(get_current_user)):
    """メンバーを会社から削除する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    ok = company_module.remove_company_member(company_id, target_user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed"}


@app.get("/api/settings", response_model=SettingsResponse)
def api_get_settings(user=Depends(get_current_user)):
    """現在の設定を返す。"""
    return SettingsResponse(auto_execute=server_settings.get_auto_execute())


@app.put("/api/settings", response_model=SettingsResponse)
def api_update_settings(body: SettingsUpdateRequest, user=Depends(get_current_user)):
    """設定を更新する。"""
    server_settings.set_auto_execute(body.auto_execute)
    return SettingsResponse(auto_execute=body.auto_execute)


@app.get("/api/runs/{run_id}/spec")
def api_run_spec(run_id: str, user=Depends(get_current_user)):
    """run_id の spec_markdown 全文を返す。"""
    run = persist.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"spec_markdown": run.get("spec_markdown") or ""}


@app.post("/run/{run_id}/implement", response_model=ImplementResponse)
def run_implement(run_id: str, background_tasks: BackgroundTasks, user=Depends(get_current_user)):
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
        background_tasks.add_task(server_alert.alert_run_failed, run_id, str(e))
        raise HTTPException(status_code=500, detail=str(e))

    final_status = result.get("status") or ""
    if final_status == "failed":
        background_tasks.add_task(
            server_alert.alert_run_failed, run_id, "; ".join(result.get("error_logs") or [])
        )
    output_subdir = result.get("output_subdir") or ""

    persist.update_run_status(run_id, {
        "status": final_status,
        "retry_count": result.get("retry_count") or 0,
        "state_snapshot": None,
    })

    company_id = user.get("company_id") if user else None
    try:
        persist.persist_features(run_id, result, company_id=company_id)
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
    if result.get("output_rules_improvement") and output_subdir:
        _write_rules_suggestions(workspace_root, output_subdir, run_id, result)
    if result.get("output_rules_improvement") and output_subdir and final_status == "published":
        rules_merge.save_pending_improvements(
            run_id=run_id,
            result=result,
            genre=result.get("genre"),
        )

    notion_page_id = result.get("notion_page_id") or snapshot.get("notion_page_id") or ""
    if notion_page_id:
        try:
            notion_client.update_page_status(
                notion_page_id, "完了済", run_id=run_id
            )
        except Exception:
            pass

    total_in = result.get("total_input_tokens") or 0
    total_out = result.get("total_output_tokens") or 0
    cost_usd, _ = server_cost.check_budget(total_in, total_out)

    return ImplementResponse(
        status=final_status,
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


# =====================================================================
# 管理コンソール (Admin) — 開発者向け
# =====================================================================


@app.get("/api/admin/runs")
def api_admin_runs(limit: int = 50, user=Depends(get_admin_user)):
    """runs テーブルから state_snapshot 含む詳細データを返す。"""
    rows = persist.get_runs_detail(limit=limit)
    enriched = []
    for row in rows:
        snapshot = row.get("state_snapshot")
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        total_in = snapshot.get("total_input_tokens") or 0
        total_out = snapshot.get("total_output_tokens") or 0
        cost_usd = 0.0
        if total_in or total_out:
            cost_usd, _ = server_cost.check_budget(total_in, total_out)
        enriched.append({
            "run_id": row.get("run_id"),
            "status": row.get("status"),
            "requirement_summary": row.get("requirement_summary"),
            "genre": row.get("genre"),
            "retry_count": row.get("retry_count") or 0,
            "error_logs": snapshot.get("error_logs") or [],
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "cost_usd": round(cost_usd, 6),
            "created_at": row.get("created_at"),
        })
    return {"runs": enriched}


@app.get("/api/admin/audit-logs")
def api_admin_audit_logs(
    run_id: Optional[str] = None, limit: int = 200, user=Depends(get_admin_user)
):
    """audit_logs テーブルを返す。run_id でフィルタ可。"""
    return {"audit_logs": persist.get_audit_logs(run_id=run_id, limit=limit)}


@app.get("/api/admin/oauth-status")
def api_admin_oauth_status(user=Depends(get_admin_user)):
    """OAuth トークンの接続状況を返す（トークン本体は含まない）。"""
    return {"oauth": persist.get_oauth_status()}


@app.get("/api/admin/kpi")
def api_admin_kpi(limit: int = 200, user=Depends(get_admin_user)):
    """KPI 集計: 成功率・平均コスト・予算超過数・ジャンル別内訳を返す。"""
    rows = persist.get_runs_detail(limit=limit)
    total = len(rows)
    published = 0
    failed = 0
    budget_exceeded_count = 0
    total_cost = 0.0
    genre_counts: dict[str, int] = {}
    alert_events: list[dict] = []

    for row in rows:
        status = row.get("status") or ""
        if status == "published":
            published += 1
        elif status in ("failed", "timeout"):
            failed += 1
            alert_events.append({
                "type": "run_failed",
                "run_id": row.get("run_id"),
                "status": status,
                "created_at": row.get("created_at"),
            })

        snapshot = row.get("state_snapshot")
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        total_in = snapshot.get("total_input_tokens") or 0
        total_out = snapshot.get("total_output_tokens") or 0
        if total_in or total_out:
            cost, exceeded = server_cost.check_budget(total_in, total_out)
            total_cost += cost
            if exceeded:
                budget_exceeded_count += 1
                alert_events.append({
                    "type": "budget_exceeded",
                    "run_id": row.get("run_id"),
                    "cost_usd": round(cost, 4),
                    "created_at": row.get("created_at"),
                })

        genre = row.get("genre") or "unknown"
        genre_counts[genre] = genre_counts.get(genre, 0) + 1

    success_rate = (published / total * 100) if total > 0 else 0.0
    avg_cost = (total_cost / total) if total > 0 else 0.0

    return {
        "total_runs": total,
        "published": published,
        "failed": failed,
        "success_rate": round(success_rate, 1),
        "avg_cost_usd": round(avg_cost, 4),
        "budget_exceeded_count": budget_exceeded_count,
        "max_cost_per_task_usd": server_cost.get_max_cost_per_task_usd(),
        "genre_breakdown": genre_counts,
        "alert_events": sorted(alert_events, key=lambda x: x.get("created_at") or "", reverse=True)[:50],
    }


@app.get("/api/admin/rule-changes")
def api_admin_rule_changes(
    status: Optional[str] = None, limit: int = 100, user=Depends(get_admin_user)
):
    """ルール改善履歴を返す。status でフィルタ可（pending / approved / rejected）。"""
    return {"rule_changes": persist.get_rule_changes(status=status, limit=limit)}


@app.post("/api/admin/rule-changes/{change_id}/approve")
def api_approve_rule_change(change_id: str, user=Depends(get_admin_user)):
    """保留中のルール改善を承認し、ルールファイルに反映する。"""
    row = persist.get_rule_change_by_id(change_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule change not found")
    if row.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot approve: status is {row.get('status')}")

    applied = rules_merge.apply_approved_change(
        workspace_root=_project_root,
        rules_dir_name="rules",
        rule_change=row,
    )

    reviewer = user.get("email") if user else None
    persist.update_rule_change_status(change_id, "approved", reviewed_by=reviewer)
    return {"status": "approved", "applied_to_file": applied}


@app.post("/api/admin/rule-changes/{change_id}/reject")
def api_reject_rule_change(change_id: str, user=Depends(get_admin_user)):
    """保留中のルール改善を却下する。"""
    row = persist.get_rule_change_by_id(change_id)
    if not row:
        raise HTTPException(status_code=404, detail="Rule change not found")
    if row.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot reject: status is {row.get('status')}")

    reviewer = user.get("email") if user else None
    persist.update_rule_change_status(change_id, "rejected", reviewed_by=reviewer)
    return {"status": "rejected"}


_ADMIN_HTML = _project_root / "server" / "static" / "admin" / "index.html"


@app.get("/admin", include_in_schema=False)
def admin_console():
    """開発者向け管理コンソール: エラーログ・コスト・監査ログ・OAuth 状況・設定。"""
    if _ADMIN_HTML.exists():
        return FileResponse(_ADMIN_HTML)
    raise HTTPException(status_code=404, detail="Admin console not found")


@app.get("/api/next-system-suggestion")
def api_next_system_suggestion(user=Depends(get_current_user)):
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
        section("Publish（publish_rules.md）", "publish_rules_improvement"),
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
def run_agent(body: RunRequest, background_tasks: BackgroundTasks, user=Depends(get_current_user)):
    """
    要件を受け取り、エージェントを実行する。
    auto_execute ON: Spec → Coder → Review → GitHub push まで一気通貫。
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

            company_id = user.get("company_id") if user else None
            try:
                persist.persist_spec_snapshot(result, company_id=company_id)
            except Exception:
                pass

            spec_preview = (result.get("spec_markdown") or "")[:500]
            total_in = (result.get("total_input_tokens") or 0) + nin_suggestor
            total_out = (result.get("total_output_tokens") or 0) + nout_suggestor
            cost_usd, budget_exceeded = server_cost.check_budget(total_in, total_out)
            return RunResponse(
                status="spec_review",
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
        background_tasks.add_task(
            server_alert.alert_run_failed,
            body.requirement or body.notion_page_id or "",
            str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))

    spec_preview = ""
    if result.get("spec_markdown"):
        spec_preview = (result["spec_markdown"] or "")[:500]

    output_subdir = result.get("output_subdir") or ""
    company_id = user.get("company_id") if user else None

    try:
        persist.persist_run(
            workspace_root=Path(body.workspace_root),
            output_subdir=output_subdir,
            result=result,
            company_id=company_id,
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
        rules_merge.save_pending_improvements(
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
        background_tasks.add_task(
            server_alert.alert_budget_exceeded, result.get("run_id", ""), cost_usd
        )

    run_status = result.get("status", "")
    if run_status == "failed":
        background_tasks.add_task(
            server_alert.alert_run_failed,
            result.get("run_id", ""),
            "; ".join(result.get("error_logs") or []),
        )

    return RunResponse(
        status=result.get("status", ""),
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
        # run_agent は FastAPI DI 前提のため、BackgroundTasks を手動生成して渡す。
        # user=None: webhook は独自の HMAC 認証済みなので認証スキップ。
        _bg = BackgroundTasks()
        run_agent(run_body, _bg, user=None)
    except Exception as e:
        logger.exception("Webhook background run failed for page_id=%s: %s", notion_page_id, e)
        try:
            server_alert.alert_run_failed(f"webhook-{notion_page_id}", str(e))
        except Exception:
            pass


@app.post("/webhook/notion")
async def webhook_notion(request: Request, background_tasks: BackgroundTasks):
    """Deprecated: use /webhook/notion/{company_id}。verification_token のみ応答。"""
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 検証リクエスト: body に verification_token のみ（初期セットアップ用）
    if set(payload.keys()) <= {"verification_token"} and "verification_token" in payload:
        return {"verification_token": payload["verification_token"]}

    raise HTTPException(
        status_code=400,
        detail="Use tenant-specific endpoint: /webhook/notion/{company_id}",
    )




@app.post("/run-from-database", response_model=RunFromDatabaseResponse)
def run_from_database(body: RunFromDatabaseRequest, user=Depends(get_current_user)):
    """
    Notion データベースの「実装希望」ステータスのページを取得し、順次 develop_agent で処理する。
    各ページの要件を取得 → ステータスを「実装中」に更新 → invoke → 完了後に「完了済」と run_id を書き戻す。
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
                rules_merge.save_pending_improvements(
                    run_id=run_id,
                    result=result,
                    genre=genre,
                )
        except Exception as e:
            status = "error"
            run_id = f"error: {str(e)[:200]}"
            try:
                server_alert.alert_run_failed(f"db-batch-{page_id}", str(e))
            except Exception:
                pass

        try:
            notion_client.update_page_status(
                page_id,
                "完了済",
                run_id=run_id,
            )
        except Exception:
            pass

        results.append(
            {
                "page_id": page_id,
                "status": status,
                "run_id": run_id,
            }
        )

    return RunFromDatabaseResponse(
        processed=len(results),
        results=results,
        message=f"{len(results)} 件処理しました",
    )


# =====================================================================
# 汎用チャンネルランナー（Slack / Chatwork / Google Drive 共通）
# =====================================================================


def _run_agent_for_channel(adapter: ChannelAdapter, msg: ChannelMessage) -> None:
    """
    チャンネルアダプタ経由でエージェントを実行する汎用バックグラウンドランナー。
    _run_agent_for_webhook() (Notion 用) と同パターン。
    """
    import asyncio

    def _send_async(coro):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(coro)
            else:
                asyncio.run(coro)
        except Exception:
            pass

    try:
        _send_async(adapter.send_progress(msg.reply_to, "要件を受け付けました。処理を開始します..."))

        run_body = RunRequest(
            requirement=msg.requirement,
            workspace_root=".",
            rules_dir="rules",
            output_rules_improvement=False,
            skip_accumulation_inject=True,
            genre=msg.genre,
        )
        _bg = BackgroundTasks()
        result_response = run_agent(run_body, _bg, user=None)

        run_id = getattr(result_response, "run_id", "")
        status = getattr(result_response, "status", "")
        _send_async(adapter.send_result(msg.reply_to, run_id, status))

    except Exception as e:
        logger.exception(
            "Channel background run failed for %s: %s", msg.source, e
        )
        try:
            server_alert.alert_run_failed(
                f"channel-{msg.source}-{msg.sender_id}", str(e)
            )
        except Exception:
            pass


# =====================================================================
# OAuth state ヘルパー — company_id を HMAC 署名付きで state に埋め込む
# =====================================================================

def _encode_oauth_state(company_id: str) -> str:
    """company_id を HMAC 署名付きで state パラメータにエンコードする。"""
    secret = os.environ.get("TOKEN_ENCRYPTION_KEY", "oauth-state-fallback")
    sig = hmac.new(secret.encode(), company_id.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{company_id}:{sig}"


def _decode_oauth_state(state: str) -> str | None:
    """state パラメータから company_id をデコード・検証する。不正なら None。"""
    if not state or ":" not in state:
        return None
    parts = state.rsplit(":", 1)
    if len(parts) != 2:
        return None
    company_id, sig = parts
    secret = os.environ.get("TOKEN_ENCRYPTION_KEY", "oauth-state-fallback")
    expected = hmac.new(secret.encode(), company_id.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, sig):
        return None
    return company_id


# =====================================================================
# OAuth pre-authorize — チャネル別 (Slack / Notion / GDrive)
# =====================================================================

_OAUTH_CHANNEL_CONFIG = {
    "slack": {
        "authorize_url": "https://slack.com/oauth/v2/authorize",
        "default_scopes": "channels:history,chat:write,files:read",
        "scope_param": "scope",
    },
    "notion": {
        "authorize_url": "https://api.notion.com/v1/oauth/authorize",
        "extra_params": "&response_type=code&owner=user",
    },
    "gdrive": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "default_scopes": "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/documents.readonly",
        "scope_param": "scope",
        "extra_params": "&response_type=code&access_type=offline&prompt=consent",
    },
}


@app.post("/api/oauth/{channel}/pre-authorize")
async def oauth_channel_pre_authorize(channel: str, user=Depends(get_current_user)):
    """チャネル OAuth の認可 URL を返す。DB にテナント設定があればそれを使用。"""
    if channel not in _OAUTH_CHANNEL_CONFIG:
        raise HTTPException(status_code=400, detail=f"Unsupported channel: {channel}")

    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    cfg = _OAUTH_CHANNEL_CONFIG[channel]

    # DB テナント設定のみ参照
    client_id = ch_config_module.get_config_value(company_id, channel, "client_id")
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"{channel} OAuth not configured. Set app credentials in the dashboard first.",
        )

    redirect_uri = ch_config_module.get_redirect_uri(channel)

    state = _encode_oauth_state(company_id)

    auth_url = f"{cfg['authorize_url']}?client_id={client_id}&redirect_uri={redirect_uri}&state={state}"

    # スコープ
    if cfg.get("scope_param"):
        scopes = cfg.get("default_scopes", "")
        auth_url += f"&{cfg['scope_param']}={scopes}"

    # チャネル固有の追加パラメータ
    if cfg.get("extra_params"):
        auth_url += cfg["extra_params"]

    return {"authorize_url": auth_url}


# =====================================================================
# OAuth エンドポイント — Notion
# =====================================================================


@app.get("/api/oauth/notion/authorize")
async def oauth_notion_authorize():
    """Deprecated: use POST /api/oauth/notion/pre-authorize."""
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated. Use the dashboard to connect.",
    )


@app.get("/api/oauth/notion/callback")
async def oauth_notion_callback(code: str, state: str = ""):
    """Notion OAuth コールバック: code を access_token に交換する。"""
    import httpx

    # state から company_id を取得（テナント別設定用）
    company_id = _decode_oauth_state(state)

    client_id = ch_config_module.get_config_value(company_id, "notion", "client_id")
    client_secret = ch_config_module.get_config_value(company_id, "notion", "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Notion OAuth credentials not configured for this tenant.",
        )
    redirect_uri = ch_config_module.get_redirect_uri("notion")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.notion.com/v1/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Notion token exchange failed: {resp.text[:200]}",
        )

    data = resp.json()
    tenant_id = company_id or "default"
    oauth_store.save_token(
        provider="notion",
        access_token=data["access_token"],
        raw_response=data,
        tenant_id=tenant_id,
    )
    from fastapi.responses import RedirectResponse
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return RedirectResponse(url=f"{base_url}/dashboard", status_code=302)


# =====================================================================
# OAuth + Webhook エンドポイント — Slack
# =====================================================================


@app.get("/api/oauth/slack/authorize")
async def oauth_slack_authorize():
    """Deprecated: use POST /api/oauth/slack/pre-authorize."""
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated. Use the dashboard to connect.",
    )


@app.get("/api/oauth/slack/callback")
async def oauth_slack_callback(code: str, state: str = ""):
    """Slack OAuth コールバック: code を access_token に交換する。"""
    import httpx

    # state から company_id を取得（テナント別設定用）
    company_id = _decode_oauth_state(state)

    client_id = ch_config_module.get_config_value(company_id, "slack", "client_id")
    client_secret = ch_config_module.get_config_value(company_id, "slack", "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Slack OAuth credentials not configured for this tenant.",
        )
    redirect_uri = ch_config_module.get_redirect_uri("slack")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=f"Slack OAuth failed: {data.get('error')}",
        )

    tenant_id = company_id or "default"
    oauth_store.save_token(
        provider="slack",
        access_token=data.get("access_token", ""),
        scopes=data.get("scope", ""),
        raw_response=data,
        tenant_id=tenant_id,
    )
    from fastapi.responses import RedirectResponse
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return RedirectResponse(url=f"{base_url}/dashboard", status_code=302)


@app.post("/webhook/slack")
async def webhook_slack(request: Request, background_tasks: BackgroundTasks):
    """Deprecated: use /webhook/slack/{company_id}。URL Verification のみ応答。"""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    raise HTTPException(
        status_code=400,
        detail="Use tenant-specific endpoint: /webhook/slack/{company_id}",
    )


# =====================================================================
# Webhook エンドポイント — Chatwork
# =====================================================================


@app.post("/webhook/chatwork")
async def webhook_chatwork(request: Request, background_tasks: BackgroundTasks):
    """Deprecated: use /webhook/chatwork/{company_id}。"""
    raise HTTPException(
        status_code=400,
        detail="Use tenant-specific endpoint: /webhook/chatwork/{company_id}",
    )


# =====================================================================
# OAuth + Webhook エンドポイント — Google Drive
# =====================================================================


@app.get("/api/oauth/gdrive/authorize")
async def oauth_gdrive_authorize():
    """Deprecated: use POST /api/oauth/gdrive/pre-authorize."""
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated. Use the dashboard to connect.",
    )


@app.get("/api/oauth/gdrive/callback")
async def oauth_gdrive_callback(code: str, state: str = ""):
    """Google OAuth コールバック: code を access_token + refresh_token に交換する。"""
    import httpx

    # state から company_id を取得（テナント別設定用）
    company_id = _decode_oauth_state(state)

    client_id = ch_config_module.get_config_value(company_id, "gdrive", "client_id")
    client_secret = ch_config_module.get_config_value(company_id, "gdrive", "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth credentials not configured for this tenant.",
        )
    redirect_uri = ch_config_module.get_redirect_uri("gdrive")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Google token exchange failed: {resp.text[:200]}",
        )

    data = resp.json()
    expires_in = int(data.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    tenant_id = company_id or "default"
    oauth_store.save_token(
        provider="gdrive",
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        scopes=data.get("scope"),
        raw_response=data,
        tenant_id=tenant_id,
    )
    from fastapi.responses import RedirectResponse
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return RedirectResponse(url=f"{base_url}/dashboard", status_code=302)


@app.post("/webhook/gdrive")
async def webhook_gdrive(request: Request, background_tasks: BackgroundTasks):
    """Deprecated: use /webhook/gdrive/{company_id}。"""
    raise HTTPException(
        status_code=400,
        detail="Use tenant-specific endpoint: /webhook/gdrive/{company_id}",
    )


@app.get("/api/gdrive/poll")
async def api_gdrive_poll(
    background_tasks: BackgroundTasks, user=Depends(get_current_user)
):
    """Google Drive フォルダポーリング: 新規/更新ドキュメントを検出して一括処理する。"""
    from server.gdrive_handler import (
        GDriveAdapter,
        fetch_doc_as_text,
        poll_folder_for_new_docs,
    )

    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    gdrive_config = ch_config_module.get_config(company_id, "gdrive") or {}
    folder_id = gdrive_config.get("watch_folder_id", "").strip()
    if not folder_id:
        raise HTTPException(
            status_code=400,
            detail="GDrive watch_folder_id not configured. Set it in the dashboard channel settings.",
        )

    docs = poll_folder_for_new_docs(
        folder_id,
        tenant_id=company_id,
        client_id=gdrive_config.get("client_id"),
        client_secret=gdrive_config.get("client_secret"),
    )
    adapter = GDriveAdapter(config=gdrive_config, tenant_id=company_id)
    queued = 0
    for doc in docs:
        content = fetch_doc_as_text(
            doc["id"],
            tenant_id=company_id,
            client_id=gdrive_config.get("client_id"),
            client_secret=gdrive_config.get("client_secret"),
        )
        if content.strip():
            msg = ChannelMessage(
                source="gdrive",
                requirement=content,
                reply_to={"doc_id": doc["id"]},
            )
            background_tasks.add_task(_run_agent_for_channel, adapter, msg)
            queued += 1

    return {"status": "ok", "docs_found": len(docs), "queued": queued}


# =====================================================================
# テナント別 Webhook エンドポイント
# =====================================================================


@app.post("/webhook/slack/{company_id}")
async def webhook_slack_tenant(company_id: str, request: Request, background_tasks: BackgroundTasks):
    """テナント別 Slack Events API Webhook。"""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    from server.slack_handler import SlackAdapter

    config = ch_config_module.get_config(company_id, "slack") or {}
    adapter = SlackAdapter(config=config, tenant_id=company_id)
    msg = await adapter.parse_webhook(request)
    if msg is None:
        return {"status": "ignored"}

    background_tasks.add_task(_run_agent_for_channel, adapter, msg)
    return {"status": "accepted"}


@app.post("/webhook/chatwork/{company_id}")
async def webhook_chatwork_tenant(company_id: str, request: Request, background_tasks: BackgroundTasks):
    """テナント別 Chatwork Webhook。"""
    from server.chatwork_handler import ChatworkAdapter

    config = ch_config_module.get_config(company_id, "chatwork") or {}
    adapter = ChatworkAdapter(config=config, tenant_id=company_id)
    msg = await adapter.parse_webhook(request)
    if msg is None:
        return {"status": "ignored"}

    background_tasks.add_task(_run_agent_for_channel, adapter, msg)
    return {"status": "accepted"}


@app.post("/webhook/notion/{company_id}")
async def webhook_notion_tenant(company_id: str, request: Request, background_tasks: BackgroundTasks):
    """テナント別 Notion Webhook。既存の Notion webhook 処理を company_id 付きで実行。"""
    raw_body = await request.body()
    signature = request.headers.get("X-Notion-Signature", "")

    config = ch_config_module.get_config(company_id, "notion") or {}
    webhook_secret = config.get("webhook_secret", "")

    if webhook_secret:
        if not _verify_notion_signature(raw_body, signature, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid Notion signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Notion webhook は page_id を含むイベントを処理
    page_id = ""
    if payload.get("type") == "page" and payload.get("page"):
        page_id = payload["page"].get("id", "")
    elif payload.get("data") and payload["data"].get("id"):
        page_id = payload["data"]["id"]

    if not page_id:
        return {"status": "ignored"}

    genre = payload.get("genre")
    background_tasks.add_task(_run_agent_for_webhook, page_id, genre)
    return {"status": "accepted"}


@app.post("/webhook/gdrive/{company_id}")
async def webhook_gdrive_tenant(company_id: str, request: Request, background_tasks: BackgroundTasks):
    """テナント別 Google Drive 手動トリガー。"""
    from server.gdrive_handler import GDriveAdapter

    config = ch_config_module.get_config(company_id, "gdrive") or {}
    adapter = GDriveAdapter(config=config, tenant_id=company_id)
    msg = await adapter.parse_webhook(request)
    if msg is None:
        return {"status": "ignored", "reason": "no doc_id or empty document"}

    background_tasks.add_task(_run_agent_for_channel, adapter, msg)
    return {"status": "accepted"}


# =====================================================================
# OAuth エンドポイント — GitHub（オンボーディング用）
# =====================================================================


async def _revoke_github_grant(access_token: str) -> bool:
    """GitHub OAuth App の認可(grant)を revoke する。
    これにより次回の OAuth フローで同意画面が再表示され、アカウント切替が可能になる。
    成功時 True、失敗時 False を返す。
    """
    import httpx

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret or not access_token:
        logger.warning("GitHub grant revoke skipped: missing client_id, client_secret, or access_token")
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.request(
                "DELETE",
                f"https://api.github.com/applications/{client_id}/grant",
                auth=(client_id, client_secret),
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"access_token": access_token},
            )
        if resp.status_code in (204, 404):
            # 204: 正常に revoke された
            # 404: grant が既に存在しない（= revoke 済みと同義）
            logger.info("GitHub grant revoke ok: status=%s", resp.status_code)
            return True
        else:
            logger.warning("GitHub grant revoke unexpected status=%s body=%s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("GitHub grant revoke failed: %s", e)
        return False


@app.post("/api/oauth/github/pre-authorize")
async def oauth_github_pre_authorize(user=Depends(get_current_user)):
    """既存の GitHub OAuth 認可を revoke し、認可 URL を返す。
    ダッシュボードから Bearer 付きで呼ぶことで company_id のトークンも revoke できる。
    """
    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if not redirect_uri:
        base = os.environ.get("BASE_URL", "").rstrip("/")
        if base:
            redirect_uri = f"{base}/api/oauth/github/callback"
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=500, detail="GitHub OAuth not configured"
        )

    revoked = False

    company_id = (user or {}).get("company_id") if user else None
    if company_id:
        # 1. disconnect 時に revoke 失敗した「revoke 待ち」トークンを再試行
        revoke_pending = oauth_store.get_token("github_revoke_pending", tenant_id=company_id)
        if revoke_pending:
            r = await _revoke_github_grant(revoke_pending.get("access_token", ""))
            if r:
                oauth_store.delete_token("github_revoke_pending", tenant_id=company_id)
                revoked = True
                logger.info("Deferred GitHub grant revoke succeeded for company_id=%s", company_id)

        # 2. company_id のトークンを revoke（まだ残っている場合）
        token_data = oauth_store.get_token("github", tenant_id=company_id)
        if token_data:
            r = await _revoke_github_grant(token_data.get("access_token", ""))
            revoked = revoked or r

    # 3. pending トークンも revoke
    pending = oauth_store.get_token("github", tenant_id="pending")
    if pending:
        r = await _revoke_github_grant(pending.get("access_token", ""))
        revoked = revoked or r
        oauth_store.delete_token("github", tenant_id="pending")

    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope=repo"
        f"&redirect_uri={redirect_uri}"
    )
    return {"authorize_url": auth_url, "revoked": revoked}


@app.get("/api/oauth/github/authorize")
async def oauth_github_authorize():
    """GitHub の OAuth 認可画面へリダイレクトする（フォールバック用）。"""
    from fastapi.responses import RedirectResponse

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    redirect_uri = os.environ.get("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if not redirect_uri:
        base = os.environ.get("BASE_URL", "").rstrip("/")
        if base:
            redirect_uri = f"{base}/api/oauth/github/callback"
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=500, detail="GitHub OAuth not configured"
        )

    # pending トークンがあれば revoke を試みる
    pending = oauth_store.get_token("github", tenant_id="pending")
    if pending:
        await _revoke_github_grant(pending.get("access_token", ""))
        oauth_store.delete_token("github", tenant_id="pending")

    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope=repo"
        f"&redirect_uri={redirect_uri}"
    )
    return RedirectResponse(url=auth_url)


@app.get("/api/oauth/github/callback")
async def oauth_github_callback(code: str, state: str = ""):
    """GitHub OAuth コールバック: code を access_token に交換し、ダッシュボードにリダイレクトする。"""
    import httpx
    from fastapi.responses import RedirectResponse

    client_id = os.environ.get("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub token exchange failed: {resp.text[:200]}",
        )

    data = resp.json()
    access_token = data.get("access_token", "")
    if not access_token:
        error = data.get("error_description", data.get("error", "unknown"))
        raise HTTPException(status_code=502, detail=f"GitHub OAuth error: {error}")

    # tenant_id を company_id にするために、一旦 "github_pending" に保存
    # provision 時に正しい company_id に移す
    oauth_store.save_token(
        provider="github",
        tenant_id="pending",
        access_token=access_token,
        scopes=data.get("scope"),
        raw_response=data,
    )

    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    return RedirectResponse(url=f"{base_url}/dashboard?github_connected=true")


# =====================================================================
# オンボーディング プロビジョニング API
# =====================================================================


@app.post("/api/onboarding/provision/github")
async def api_provision_github(user=Depends(get_current_user)):
    """GitHub リポ作成 + ボイラープレート push。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    company = company_module.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # pending から GitHub トークンを取得
    token_data = oauth_store.get_token("github", tenant_id="pending")
    if not token_data:
        # company_id で直接も試す
        token_data = oauth_store.get_token("github", tenant_id=company_id)
    if not token_data:
        raise HTTPException(status_code=400, detail="GitHub not connected. Please authorize first.")

    access_token = token_data["access_token"]
    result = await onboarding_provisioner.provision_github(
        company_id=company_id,
        access_token=access_token,
        company_slug=company["slug"],
        company_name=company.get("name", company["slug"]),
    )

    # pending トークンを削除（provision_github 内で正しい tenant_id で保存済み）
    oauth_store.delete_token("github", tenant_id="pending")

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "GitHub provisioning failed"))

    return result


@app.post("/api/onboarding/provision/supabase")
async def api_provision_supabase(body: TokenInput, user=Depends(get_current_user)):
    """Supabase プロジェクト作成 + テーブル初期化。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    company = company_module.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    result = await onboarding_provisioner.provision_supabase(
        company_id=company_id,
        access_token=body.access_token,
        company_slug=company["slug"],
        expires_at=body.expires_at,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Supabase provisioning failed"))

    return result


@app.post("/api/onboarding/provision/supabase/retry-tables")
async def api_retry_supabase_tables(user=Depends(get_current_user)):
    """Supabase テーブル初期化のリトライ。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    token_data = oauth_store.get_token("supabase", tenant_id=company_id)
    if not token_data:
        raise HTTPException(status_code=400, detail="No Supabase token found. Please reconnect.")

    result = await onboarding_provisioner.retry_supabase_tables(
        company_id=company_id,
        access_token=token_data["access_token"],
    )

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Table init retry failed"))

    return result


@app.post("/api/onboarding/provision/vercel")
async def api_provision_vercel(body: VercelProvisionRequest, user=Depends(get_current_user)):
    """Vercel プロジェクト作成 + GitHub 連携 + 環境変数設定。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    company = company_module.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    infra = company_module.get_company_infra(company_id)
    github_repo = infra.get("github_repository", "")
    supabase_url = infra.get("client_supabase_url", "")

    result = await onboarding_provisioner.provision_vercel(
        company_id=company_id,
        access_token=body.access_token,
        company_slug=company["slug"],
        github_repo=github_repo,
        supabase_url=supabase_url,
        supabase_anon_key=body.supabase_anon_key,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Vercel provisioning failed"))

    return result


@app.get("/api/integrations/vercel/deploy-status")
async def api_vercel_deploy_status(user=Depends(get_current_user)):
    """Vercel の最新デプロイ状態を返す。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        return {"state": "UNKNOWN", "ready": False}

    vercel_token = company_module.get_infra_token(company_id, "vercel_token_enc")
    if not vercel_token:
        return {"state": "NO_TOKEN", "ready": False}

    company = company_module.get_company_by_id(company_id)
    if not company:
        return {"state": "UNKNOWN", "ready": False}

    project_name = f"develop_agent-{company['slug']}"
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.vercel.com/v6/deployments",
                headers={"Authorization": f"Bearer {vercel_token}"},
                params={"project": project_name, "limit": "1", "target": "production"},
            )
            if resp.status_code == 200:
                deploys = resp.json().get("deployments", [])
                if deploys:
                    state = deploys[0].get("state", deploys[0].get("readyState", "UNKNOWN"))
                    return {"state": state, "ready": state == "READY"}
                return {"state": "NO_DEPLOYMENTS", "ready": False}
    except Exception:
        pass
    return {"state": "UNKNOWN", "ready": False}


# ---------- チャネル設定 CRUD ----------

from server import channel_config as ch_config_module  # noqa: E402


@app.get("/api/integrations/channel-config/{channel}")
async def api_get_channel_config(channel: str, user=Depends(get_current_user)):
    """チャネル設定をマスク済みで返す。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    if channel not in ch_config_module.CHANNEL_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid channel")
    masked = ch_config_module.get_masked_config(company_id, channel)
    # instance_url が channel_configs に無い場合、company_saas_connections からフォールバック
    if masked and "instance_url" in ch_config_module.CHANNEL_FIELDS.get(channel, []) and not masked.get("instance_url"):
        conn = saas_connection.get_connection_by_saas(company_id, channel)
        if conn and conn.get("instance_url"):
            masked["instance_url"] = conn["instance_url"]
    return {
        "has_config": masked is not None,
        "fields": masked or {},
        "required_fields": ch_config_module.CHANNEL_FIELDS[channel],
    }


@app.post("/api/integrations/channel-config/{channel}")
async def api_save_channel_config(channel: str, request: Request, user=Depends(get_current_user)):
    """チャネル設定を保存する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    if channel not in ch_config_module.CHANNEL_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid channel")
    body = await request.json()
    ok = ch_config_module.save_config(company_id, channel, body)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save config")
    return {"ok": True}


@app.delete("/api/integrations/channel-config/{channel}")
async def api_delete_channel_config(channel: str, user=Depends(get_current_user)):
    """チャネル設定を削除する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    if channel not in ch_config_module.CHANNEL_FIELDS:
        raise HTTPException(status_code=400, detail="Invalid channel")
    ch_config_module.delete_config(company_id, channel)
    return {"ok": True}


@app.get("/api/onboarding/status")
async def api_onboarding_status(user=Depends(get_current_user)):
    """各サービスの接続状態をまとめて返す。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    onboarding = company_module.get_onboarding(company_id)
    infra = company_module.get_company_infra(company_id)

    github_token = oauth_store.get_token("github", tenant_id=company_id)
    github_connected = github_token is not None

    return {
        "onboarding": onboarding,
        "infra": infra,
        "services": {
            "github": {
                "connected": github_connected,
                "repo": infra.get("github_repository", ""),
            },
            "supabase": {
                "connected": bool(infra.get("client_supabase_url")),
                "url": infra.get("client_supabase_url", ""),
            },
            "vercel": {
                "connected": bool(infra.get("vercel_project_url")),
                "url": infra.get("vercel_project_url", ""),
            },
        },
    }


# ---------- 連携設定 API ----------


def _require_admin_or_owner(user: dict):
    """company_role が admin または owner でなければ 403 を返す。"""
    role = (user or {}).get("company_role", "member")
    if role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Admin or owner role required")


@app.get("/api/integrations/status")
async def api_integrations_status(user=Depends(get_current_user)):
    """全連携サービスの接続状態を返す。"""
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    infra = company_module.get_company_infra(company_id) or {}

    # OAuth トークンの有無で接続状態を判定（1 クエリで全プロバイダ取得）
    providers = ["notion", "slack", "gdrive", "chatwork", "github"]
    tokens_bulk = oauth_store.get_tokens_bulk(providers, [company_id, "default"])
    services = {}
    for provider in providers:
        token = tokens_bulk.get((provider, company_id)) or tokens_bulk.get((provider, "default"))
        services[provider] = {
            "connected": token is not None,
            "expires_at": token.get("expires_at") if token else None,
            "scopes": token.get("scopes") if token else None,
            "updated_at": token.get("updated_at") if token else None,
        }

    # 環境変数の設定状態（インフラ用のみ）
    env_status = {
        "GITHUB_CLIENT_ID": bool(os.environ.get("GITHUB_CLIENT_ID", "").strip()),
    }

    # テナント別チャネル設定の状態
    all_ch_configs = ch_config_module.get_all_configs(company_id)
    channel_configs = {}
    for ch, fields in ch_config_module.CHANNEL_FIELDS.items():
        ch_cfg = all_ch_configs.get(ch)
        ready_field = "api_token" if ch == "chatwork" else "client_id"
        channel_configs[ch] = {
            "has_config": ch in all_ch_configs,
            "is_ready": bool(ch_cfg and ch_cfg.get(ready_field)),
            "fields": fields,
        }

    # テナント別 Webhook URL
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    webhook_urls = {
        "slack": f"{base_url}/webhook/slack/{company_id}",
        "chatwork": f"{base_url}/webhook/chatwork/{company_id}",
        "notion": f"{base_url}/webhook/notion/{company_id}",
        "gdrive": f"{base_url}/webhook/gdrive/{company_id}",
    }

    # OAuth リダイレクト URI（各プロバイダーの設定画面で「承認済みリダイレクト URI」に登録する値）
    redirect_uris = {
        ch: ch_config_module.get_redirect_uri(ch)
        for ch in ["notion", "slack", "gdrive"]
    }

    return {
        "infra": infra,
        "services": services,
        "env_status": env_status,
        "company_role": (user or {}).get("company_role", "member"),
        "channel_configs": channel_configs,
        "webhook_urls": webhook_urls,
        "redirect_uris": redirect_uris,
    }


@app.post("/api/integrations/chatwork/token")
async def api_save_chatwork_token(request: Request, user=Depends(get_current_user)):
    """Chatwork API トークンを oauth_store に保存する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    body = await request.json()
    token_value = (body.get("token") or "").strip()
    if not token_value:
        raise HTTPException(status_code=400, detail="Token is required")

    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    tenant = company_id or "default"

    oauth_store.save_token(
        provider="chatwork",
        access_token=token_value,
        tenant_id=tenant,
    )

    return {"ok": True}


@app.post("/api/integrations/infra-token")
async def api_save_infra_token(request: Request, user=Depends(get_current_user)):
    """インフラトークン（Supabase Management / Vercel）を暗号化して保存する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    body = await request.json()
    field = (body.get("field") or "").strip()
    token_value = (body.get("token") or "").strip()
    expires_at = (body.get("expires_at") or "").strip()

    allowed = {"supabase_mgmt_token_enc", "vercel_token_enc"}
    if field not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid field: {field}")
    if not token_value:
        raise HTTPException(status_code=400, detail="Token is required")

    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    ok = company_module.save_infra_token(company_id, field, token_value, expires_at=expires_at)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save token")

    return {"ok": True}


@app.put("/api/integrations/infra-token/expires")
async def api_update_infra_token_expires(request: Request, user=Depends(get_current_user)):
    """インフラトークンの有効期限のみを更新する（トークン再入力不要）。admin/owner のみ。"""
    _require_admin_or_owner(user)
    body = await request.json()
    field = (body.get("field") or "").strip()
    expires_at = (body.get("expires_at") or "").strip()

    allowed = {"supabase_mgmt_token_enc", "vercel_token_enc"}
    if field not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid field: {field}")

    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    ok = company_module.update_infra_token_expires(company_id, field, expires_at)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update expiration")

    return {"ok": True}


@app.post("/api/integrations/disconnect")
async def api_disconnect_infra(request: Request, user=Depends(get_current_user)):
    """インフラ接続を解除する（GitHub / Supabase / Vercel）。admin/owner のみ。"""
    _require_admin_or_owner(user)
    body = await request.json()
    service = (body.get("service") or "").strip().lower()

    allowed = {"github", "supabase", "vercel"}
    if service not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid service: {service}")

    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    # GitHub の場合は OAuth 認可自体を revoke（次回 OAuth で同意画面を再表示させる）
    if service == "github":
        token_data = oauth_store.get_token("github", tenant_id=company_id)
        if token_data:
            access_token = token_data.get("access_token", "")
            revoked = await _revoke_github_grant(access_token)
            if not revoked and access_token:
                # revoke 失敗時: トークンを「revoke 待ち」として保存し、
                # 次回 pre-authorize で再試行できるようにする
                oauth_store.save_token(
                    provider="github_revoke_pending",
                    tenant_id=company_id,
                    access_token=access_token,
                )
                logger.info("GitHub token saved as revoke_pending for company_id=%s", company_id)
        # pending トークンも掃除
        pending = oauth_store.get_token("github", tenant_id="pending")
        if pending:
            await _revoke_github_grant(pending.get("access_token", ""))
            oauth_store.delete_token("github", tenant_id="pending")

    # oauth_store からもトークン削除
    oauth_store.delete_token(service, tenant_id=company_id)

    ok = company_module.disconnect_infra(company_id, service)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to disconnect")

    return {"ok": True}


@app.post("/api/integrations/reconnect/supabase")
async def api_reconnect_supabase(body: TokenInput, user=Depends(get_current_user)):
    """Supabase を切断→再プロビジョニングする。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    company = company_module.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # 1. 旧接続を切断
    oauth_store.delete_token("supabase", tenant_id=company_id)
    company_module.disconnect_infra(company_id, "supabase")

    # 2. 新トークンで再プロビジョニング
    result = await onboarding_provisioner.provision_supabase(
        company_id=company_id,
        access_token=body.access_token,
        company_slug=company["slug"],
    )

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Supabase provisioning failed"))

    return result


@app.post("/api/integrations/reconnect/vercel")
async def api_reconnect_vercel(body: VercelProvisionRequest, user=Depends(get_current_user)):
    """Vercel を切断→再プロビジョニングする。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = getattr(user, "company_id", None)
    if not company_id:
        company_id = (user or {}).get("company_id") if isinstance(user, dict) else None
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    company = company_module.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # 1. 旧接続を切断
    oauth_store.delete_token("vercel", tenant_id=company_id)
    company_module.disconnect_infra(company_id, "vercel")

    # 2. 新トークンで再プロビジョニング
    infra = company_module.get_company_infra(company_id)
    github_repo = infra.get("github_repository", "")
    supabase_url = infra.get("client_supabase_url", "")

    result = await onboarding_provisioner.provision_vercel(
        company_id=company_id,
        access_token=body.access_token,
        company_slug=company["slug"],
        github_repo=github_repo,
        supabase_url=supabase_url,
        supabase_anon_key=body.supabase_anon_key,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Vercel provisioning failed"))

    return result


# =====================================================================
# SaaS 接続管理 + 操作エンドポイント
# =====================================================================

from server import saas_connection  # noqa: E402
from server.saas_mcp.registry import get_adapter_class, list_supported_saas  # noqa: E402


@app.get("/api/saas/supported")
async def api_saas_supported(user=Depends(get_current_user)):
    """対応している SaaS 一覧を返す。"""
    return {"saas_list": list_supported_saas()}


@app.get("/api/saas/connections")
async def api_saas_connections(user=Depends(get_current_user)):
    """企業の SaaS 接続一覧を返す。"""
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    connections = saas_connection.get_connections(company_id)
    return {"connections": connections}


@app.post("/api/saas/connections")
async def api_create_saas_connection(request: Request, user=Depends(get_current_user)):
    """SaaS 接続を新規作成する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    body = await request.json()
    saas_name = (body.get("saas_name") or "").strip()
    if not saas_name:
        raise HTTPException(status_code=400, detail="saas_name is required")

    adapter_cls = get_adapter_class(saas_name)
    if not adapter_cls:
        raise HTTPException(status_code=400, detail=f"Unsupported SaaS: {saas_name}")

    # 既存の接続があれば再利用（重複レコード防止）
    existing = saas_connection.get_connection_by_saas(company_id, saas_name)
    if existing:
        updates = {"status": "pending"}
        if body.get("instance_url"):
            updates["instance_url"] = body["instance_url"]
        saas_connection.update_connection(existing["id"], updates)
        conn = saas_connection.get_connection(existing["id"])
    else:
        conn = saas_connection.create_connection(
            company_id=company_id,
            saas_name=saas_name,
            genre=adapter_cls.genre,
            auth_method=body.get("auth_method", adapter_cls.supported_auth_methods[0].value),
            mcp_server_type=adapter_cls.mcp_server_type,
            department=body.get("department"),
            instance_url=body.get("instance_url"),
            scopes=body.get("scopes"),
            mcp_server_config=body.get("mcp_server_config"),
        )
    if not conn:
        raise HTTPException(status_code=500, detail="Failed to create SaaS connection")
    return {"ok": True, "connection": conn}


@app.get("/api/saas/connections/{connection_id}")
async def api_get_saas_connection(connection_id: str, user=Depends(get_current_user)):
    """SaaS 接続の詳細を返す。"""
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    conn = saas_connection.get_connection(connection_id, company_id=company_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"connection": conn}


@app.delete("/api/saas/connections/{connection_id}")
async def api_delete_saas_connection(connection_id: str, user=Depends(get_current_user)):
    """SaaS 接続を削除する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    conn = saas_connection.get_connection(connection_id, company_id=company_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    # BPO タスクの connection_id 参照を解除（外部キー制約対策）
    from server.saas.task_persist import _get_client as _get_task_client
    try:
        tc = _get_task_client()
        if tc:
            tc.table("saas_tasks").update({"connection_id": None}).eq("connection_id", connection_id).execute()
    except Exception:
        pass

    # OAuth トークンも削除
    provider = f"saas_{conn['saas_name']}"
    oauth_store.delete_token(provider, tenant_id=company_id)

    ok = saas_connection.delete_connection(connection_id, company_id=company_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete connection")
    return {"ok": True}


@app.get("/api/saas/connections/{connection_id}/tools")
async def api_saas_tools(connection_id: str, user=Depends(get_current_user)):
    """SaaS 接続で利用可能なツール一覧を返す。"""
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    conn = saas_connection.get_connection(connection_id, company_id=company_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    from server.saas_mcp import get_adapter
    adapter = get_adapter(conn["saas_name"])
    if not adapter:
        raise HTTPException(status_code=400, detail=f"No adapter for: {conn['saas_name']}")

    tools = await adapter.get_available_tools()
    return {
        "tools": [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
    }


@app.post("/api/saas/connections/{connection_id}/execute")
async def api_saas_execute(connection_id: str, request: Request, user=Depends(get_current_user)):
    """SaaS ツールを実行する。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    body = await request.json()
    tool_name = (body.get("tool_name") or "").strip()
    arguments = body.get("arguments") or {}
    if not tool_name:
        raise HTTPException(status_code=400, detail="tool_name is required")

    from server.saas_executor import execute_saas_operation

    try:
        result = await execute_saas_operation(
            company_id=company_id,
            connection_id=connection_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/saas/connections/{connection_id}/refresh")
async def api_saas_refresh_token(connection_id: str, user=Depends(get_current_user)):
    """SaaS トークンを手動でリフレッシュする。admin/owner のみ。"""
    _require_admin_or_owner(user)
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    from server import token_refresh
    success = await token_refresh.refresh_single(connection_id, company_id)
    if not success:
        raise HTTPException(status_code=502, detail="Token refresh failed")
    return {"ok": True}


@app.get("/api/saas/connections/{connection_id}/audit-logs")
async def api_saas_audit_logs(connection_id: str, user=Depends(get_current_user)):
    """SaaS 接続の監査ログを返す。"""
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")
    logs = persist.get_saas_audit_logs(company_id=company_id, connection_id=connection_id)
    return {"audit_logs": logs}


# ---------- SaaS OAuth フロー ----------


@app.post("/api/oauth/saas/{saas_name}/pre-authorize")
async def oauth_saas_pre_authorize(saas_name: str, request: Request, user=Depends(get_current_user)):
    """SaaS OAuth の認可 URL を返す。"""
    _require_admin_or_owner(user)
    company_id = (user or {}).get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=400, detail="No company associated")

    adapter_cls = get_adapter_class(saas_name)
    if not adapter_cls:
        raise HTTPException(status_code=400, detail=f"Unsupported SaaS: {saas_name}")

    # テナント設定から client_id を取得
    client_id = ch_config_module.get_config_value(company_id, saas_name, "client_id")
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"{saas_name} OAuth not configured. Set client_id in channel settings first.",
        )

    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    redirect_uri = f"{base_url}/api/oauth/saas/{saas_name}/callback"
    state = _encode_oauth_state(company_id)

    adapter = adapter_cls()
    auth_url = adapter.get_oauth_authorize_url(redirect_uri, state)
    if not auth_url:
        raise HTTPException(status_code=400, detail=f"{saas_name} does not support OAuth")

    # {CLIENT_ID} プレースホルダを置換
    auth_url = auth_url.replace("{CLIENT_ID}", client_id)

    # {subdomain} プレースホルダを置換（kintone / smarthr など）
    if "{subdomain}" in auth_url:
        inst_url = ch_config_module.get_config_value(company_id, saas_name, "instance_url")
        if not inst_url:
            conn = saas_connection.get_connection_by_saas(company_id, saas_name)
            inst_url = (conn.get("instance_url", "") if conn else "")
        if not inst_url:
            raise HTTPException(
                status_code=400,
                detail="instance_url が設定されていません。先に設定を保存してください。",
            )
        m = re.match(r"https?://([^.]+)\.", inst_url)
        if not m:
            raise HTTPException(
                status_code=400,
                detail=f"instance_url の形式が不正です: {inst_url}",
            )
        auth_url = auth_url.replace("{subdomain}", m.group(1))

    return {"authorize_url": auth_url}


@app.get("/api/oauth/saas/{saas_name}/callback")
async def oauth_saas_callback(saas_name: str, code: str, state: str = ""):
    """SaaS OAuth コールバック: code を access_token に交換する。"""
    import httpx

    company_id = _decode_oauth_state(state)
    if not company_id:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    adapter_cls = get_adapter_class(saas_name)
    if not adapter_cls:
        raise HTTPException(status_code=400, detail=f"Unsupported SaaS: {saas_name}")

    # テナント設定から認証情報を取得
    client_id = ch_config_module.get_config_value(company_id, saas_name, "client_id")
    client_secret = ch_config_module.get_config_value(company_id, saas_name, "client_secret")
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail=f"{saas_name} OAuth credentials not configured")

    base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    redirect_uri = f"{base_url}/api/oauth/saas/{saas_name}/callback"

    # トークンエンドポイントを決定
    from server.token_refresh import _TOKEN_ENDPOINTS
    token_url = _TOKEN_ENDPOINTS.get(saas_name)

    # instance_url ベースの SaaS は channel_configs → 接続情報の順で取得
    if not token_url:
        instance_url = ch_config_module.get_config_value(company_id, saas_name, "instance_url")
        if not instance_url:
            conn = saas_connection.get_connection_by_saas(company_id, saas_name)
            instance_url = conn.get("instance_url", "") if conn else ""
        instance_url = instance_url.rstrip("/")
        if saas_name == "kintone" and instance_url:
            token_url = f"{instance_url}/oauth2/token"
        elif saas_name == "smarthr" and instance_url:
            token_url = f"{instance_url}/oauth/token"

    if not token_url:
        raise HTTPException(status_code=500, detail=f"Token endpoint unknown for {saas_name}")

    # code → token 交換
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"{saas_name} OAuth token exchange failed: {resp.text[:200]}",
        )

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail=f"{saas_name}: no access_token in response")

    # 期限計算
    expires_at = None
    if "expires_in" in data:
        ts = datetime.now(timezone.utc).timestamp() + data["expires_in"]
        expires_at = datetime.fromtimestamp(ts, tz=timezone.utc)

    # トークン保存
    provider = f"saas_{saas_name}"
    oauth_store.save_token(
        provider=provider,
        access_token=access_token,
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        scopes=data.get("scope"),
        raw_response=data,
        tenant_id=company_id,
    )

    # 接続ステータスを active に更新
    conn = saas_connection.get_connection_by_saas(company_id, saas_name)
    if conn:
        saas_connection.update_status(conn["id"], "active")

    # ダッシュボードにリダイレクト
    from fastapi.responses import RedirectResponse
    dashboard_url = f"{base_url}/dashboard"
    return RedirectResponse(url=dashboard_url, status_code=302)


# ── BPO タスク管理 API ──────────────────────────────────────


class BPOTaskCreateRequest(BaseModel):
    connection_id: str
    task_description: str
    dry_run: bool = False


@app.post("/api/bpo/tasks")
async def api_bpo_create_task(
    body: BPOTaskCreateRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """BPO タスクを作成し、計画生成を開始する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    # 接続情報を取得して saas_name を確定
    conn = saas_connection.get_connection(body.connection_id, company_id=company_id)
    if not conn:
        raise HTTPException(404, "SaaS接続が見つかりません")

    saas_name = conn.get("saas_name", "")
    genre = conn.get("genre", "")

    from server.saas.task_persist import create_task
    task_row = create_task(
        company_id=company_id,
        connection_id=body.connection_id,
        task_description=body.task_description,
        saas_name=saas_name,
        genre=genre,
        dry_run=body.dry_run,
    )
    if not task_row:
        raise HTTPException(500, "タスク作成に失敗しました")

    task_id = task_row["task_id"]

    # バックグラウンドで計画生成を実行
    background_tasks.add_task(_run_bpo_plan, task_id, company_id, body.connection_id, body.task_description, saas_name, genre, body.dry_run)

    return {"task_id": task_id, "status": "planning"}


def _run_bpo_plan(task_id: str, company_id: str, connection_id: str, task_description: str, saas_name: str, genre: str, dry_run: bool):
    """バックグラウンドで BPO 計画を生成する。"""
    try:
        from agent.state import initial_bpo_state
        from bpo_agent.graph import invoke_bpo_plan
        from server.saas.task_persist import save_plan, record_failure

        # SaaS のツール一覧を取得
        available_tools = []
        try:
            from server.saas.mcp.registry import get_adapter_class
            adapter_cls = get_adapter_class(saas_name)
            if adapter_cls:
                adapter = adapter_cls()
                import asyncio
                tools = asyncio.run(adapter.get_available_tools())
                available_tools = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]
        except Exception:
            logger.warning("BPO plan: ツール一覧取得に失敗 (saas=%s)", saas_name, exc_info=True)

        if not available_tools:
            logger.warning("BPO plan: ツール一覧が空です (saas=%s)。計画精度が低下する可能性があります", saas_name)

        # SaaS に接続してコンテキスト情報（アプリ一覧等）を取得
        saas_context = ""
        try:
            saas_context = asyncio.run(
                _fetch_saas_context(company_id, connection_id, saas_name)
            )
        except Exception:
            logger.warning("BPO plan: SaaS コンテキスト取得に失敗 (saas=%s)", saas_name, exc_info=True)

        state = initial_bpo_state(
            task_description=task_description,
            company_id=company_id,
            saas_connection_id=connection_id,
            saas_name=saas_name,
            genre=genre,
            dry_run=dry_run,
        )
        state["saas_available_tools"] = available_tools
        state["saas_context"] = saas_context
        state["saas_task_id"] = task_id

        result = invoke_bpo_plan(state)

        if result.get("status") == "awaiting_approval":
            save_plan(
                task_id,
                result.get("saas_plan_markdown", ""),
                result.get("saas_operations", []),
            )
        else:
            error_msg = "; ".join(result.get("error_logs") or ["計画生成に失敗"])
            record_failure(task_id, error_msg, "planning_error")

    except Exception as e:
        logger.exception("BPO plan failed for task %s", task_id)
        from server.saas.task_persist import record_failure
        import traceback
        record_failure(task_id, str(e), "planning_error", failure_detail=traceback.format_exc())


async def _fetch_saas_context(company_id: str, connection_id: str, saas_name: str) -> str:
    """SaaS に接続してコンテキスト情報（アプリ一覧等）を取得する。"""
    from server.saas.executor import SaaSExecutor

    # connection_id がない場合は saas_name から検索
    if not connection_id:
        conn = saas_connection.get_connection_by_saas(company_id, saas_name)
        if conn:
            connection_id = conn["id"]
    if not connection_id:
        return ""

    executor = SaaSExecutor(company_id=company_id, connection_id=connection_id)
    try:
        await executor.initialize()

        if saas_name == "kintone":
            # kintone: アプリ一覧を取得してコンテキストに含める
            try:
                apps_result = await executor.execute("kintone_get_apps", {})
                apps = apps_result.get("apps", [])
                if apps:
                    lines = []
                    for app in apps[:30]:  # 最大30件
                        app_id = app.get("appId", "")
                        name = app.get("name", "")
                        desc = app.get("description", "")[:50]
                        lines.append(f"  - appId: {app_id}, 名前: {name}" + (f" ({desc})" if desc else ""))
                    return "## kintone アプリ一覧（実際のアプリID）\n以下のアプリIDを操作計画で使用してください。プレースホルダー（<app_id>等）は使わないでください。\n" + "\n".join(lines)
            except Exception:
                logger.warning("kintone アプリ一覧取得失敗", exc_info=True)

        return ""
    finally:
        await executor.close()


@app.get("/api/bpo/tasks")
def api_bpo_list_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    user=Depends(get_current_user),
):
    """BPO タスク一覧を取得する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_tasks
    tasks = get_tasks(company_id, status=status, limit=limit)
    return {"tasks": tasks}


@app.get("/api/bpo/tasks/{task_id}")
def api_bpo_get_task(task_id: str, user=Depends(get_current_user)):
    """BPO タスク詳細を取得する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_task
    task = get_task(task_id, company_id=company_id)
    if not task:
        raise HTTPException(404, "タスクが見つかりません")
    return task


@app.post("/api/bpo/tasks/{task_id}/approve")
async def api_bpo_approve_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """BPO タスクを承認して実行を開始する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_task, approve_task
    task = get_task(task_id, company_id=company_id)
    if not task:
        raise HTTPException(404, "タスクが見つかりません")
    if task.get("status") != "awaiting_approval":
        raise HTTPException(400, f"承認できないステータスです: {task.get('status')}")

    approve_task(task_id)
    background_tasks.add_task(_run_bpo_exec, task_id, task)

    return {"ok": True, "status": "executing"}


def _run_bpo_exec(task_id: str, task: dict):
    """バックグラウンドで BPO タスクを実行する。"""
    import time
    start = time.time()
    try:
        from agent.state import BPOState
        from bpo_agent.graph import invoke_bpo_exec
        from server.saas.task_persist import save_result, record_failure
        from server.saas.learning import check_and_generate_rules
        import json

        operations = task.get("planned_operations") or []
        if isinstance(operations, str):
            operations = json.loads(operations)

        if not operations:
            record_failure(task_id, "実行する操作がありません（planned_operations が空）", "no_operations")
            return

        # connection_id が NULL の場合、saas_name から現在の接続を探す
        connection_id = task.get("connection_id") or ""
        if not connection_id:
            company_id = task.get("company_id", "")
            saas_name = task.get("saas_name", "")
            if company_id and saas_name:
                conn = saas_connection.get_connection_by_saas(company_id, saas_name)
                if conn:
                    connection_id = conn["id"]
                    logger.info("connection_id を復元: %s → %s", saas_name, connection_id)
        if not connection_id:
            record_failure(task_id, "SaaS接続が見つかりません。再接続してください。", "no_connection")
            return

        state = BPOState(
            run_id=task_id,
            company_id=task.get("company_id", ""),
            status="executing",
            error_logs=[],
            task_description=task.get("task_description", ""),
            saas_task_id=task_id,
            saas_connection_id=connection_id,
            saas_name=task.get("saas_name", ""),
            genre=task.get("genre", ""),
            dry_run=task.get("dry_run", False),
            saas_operations=operations,
            saas_results=[],
            saas_report_markdown="",
        )

        result = invoke_bpo_exec(state)

        duration_ms = int((time.time() - start) * 1000)
        status = result.get("status", "completed")
        results = result.get("saas_results") or []
        report = result.get("saas_report_markdown", "")

        # 結果サマリーを集計
        success_count = 0
        failure_count = 0
        errors = []
        for r in results:
            res = r.get("result", {})
            if res.get("success", True) and not res.get("error"):
                success_count += 1
            else:
                failure_count += 1
                err_msg = res.get("error", "unknown error")
                errors.append(f"{r.get('tool_name', '?')}: {err_msg}")

        summary = {
            "success_count": success_count,
            "failure_count": failure_count,
            "total_operations": len(results),
            "errors": errors,
        }

        error_logs = result.get("error_logs") or []
        has_errors = failure_count > 0 or status == "failed" or error_logs

        if has_errors:
            failure_reason = "; ".join(errors) if errors else "; ".join(error_logs) or "実行中にエラーが発生しました"
            record_failure(task_id, failure_reason, "exec_error")
            save_result(task_id, summary, report, duration_ms, status="failed")
            check_and_generate_rules(task.get("saas_name"))
        else:
            save_result(task_id, summary, report, duration_ms, status="completed")

    except Exception as e:
        logger.exception("BPO exec failed for task %s", task_id)
        from server.saas.task_persist import record_failure
        import traceback
        record_failure(task_id, str(e), "exec_error", failure_detail=traceback.format_exc())


@app.post("/api/bpo/tasks/{task_id}/reject")
def api_bpo_reject_task(task_id: str, user=Depends(get_current_user)):
    """BPO タスクを却下する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_task, reject_task
    task = get_task(task_id, company_id=company_id)
    if not task:
        raise HTTPException(404, "タスクが見つかりません")
    if task.get("status") != "awaiting_approval":
        raise HTTPException(400, f"却下できないステータスです: {task.get('status')}")

    reject_task(task_id)
    return {"ok": True, "status": "rejected"}


@app.delete("/api/bpo/tasks/{task_id}")
def api_bpo_delete_task(task_id: str, user=Depends(get_current_user)):
    """BPO タスクを削除する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_task, delete_task
    task = get_task(task_id, company_id=company_id)
    if not task:
        raise HTTPException(404, "タスクが見つかりません")
    if task.get("status") == "executing":
        raise HTTPException(400, "実行中のタスクは削除できません")

    delete_task(task_id)
    return {"ok": True}


@app.post("/api/bpo/tasks/{task_id}/retry")
async def api_bpo_retry_task(
    task_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """BPO タスクを再実行する（内容を変更して再計画）。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_task, update_task
    task = get_task(task_id, company_id=company_id)
    if not task:
        raise HTTPException(404, "タスクが見つかりません")
    if task.get("status") == "executing":
        raise HTTPException(400, "実行中のタスクは再実行できません")

    body = await request.json()
    new_description = (body.get("task_description") or "").strip()
    new_dry_run = body.get("dry_run", task.get("dry_run", False))

    updates = {
        "status": "planning",
        "plan_markdown": None,
        "planned_operations": "[]",
        "operation_count": 0,
        "result_summary": None,
        "report_markdown": None,
        "failure_reason": None,
        "failure_category": None,
        "failure_detail": None,
        "approved_at": None,
        "completed_at": None,
        "duration_ms": None,
        "dry_run": new_dry_run,
    }
    if new_description:
        updates["task_description"] = new_description
    update_task(task_id, updates)

    saas_name = task.get("saas_name", "")
    genre = task.get("genre", "")
    connection_id = task.get("connection_id", "")
    desc = new_description or task.get("task_description", "")

    background_tasks.add_task(
        _run_bpo_plan, task_id, company_id, connection_id, desc, saas_name, genre, new_dry_run
    )
    return {"ok": True, "task_id": task_id, "status": "planning"}


@app.get("/api/bpo/dashboard-summary")
def api_bpo_dashboard_summary(user=Depends(get_current_user)):
    """BPO ダッシュボード用サマリーを取得する。"""
    company_id = (user or {}).get("company_id")
    if not company_id:
        raise HTTPException(401, "認証が必要です")

    from server.saas.task_persist import get_dashboard_summary
    return get_dashboard_summary(company_id)

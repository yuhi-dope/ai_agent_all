"""国際化 (i18n) — API ドキュメントとダッシュボードの多言語対応。
現在サポート: ja (日本語, デフォルト), en (English)
"""

from __future__ import annotations

SUPPORTED_LANGUAGES = ("ja", "en")
DEFAULT_LANGUAGE = "ja"


def resolve_lang(lang: str | None) -> str:
    """クエリパラメータを正規化し、サポート外なら DEFAULT_LANGUAGE を返す。"""
    if lang and lang.strip().lower() in SUPPORTED_LANGUAGES:
        return lang.strip().lower()
    return DEFAULT_LANGUAGE


# =====================================================================
# OpenAPI (Swagger / ReDoc) 翻訳
# キー = "METHOD path" (例: "GET /health")
# 日本語は docstring から自動取得されるため、英語のみ定義。
# =====================================================================

_OPENAPI_EN: dict[str, dict[str, str]] = {
    # --- Core ---
    "GET /health": {
        "summary": "Health Check",
        "description": "Returns server health status.",
    },
    "GET /api/auth/config": {
        "summary": "Auth Configuration",
        "description": "Returns authentication configuration (public info only) for the frontend.",
    },
    "GET /api/runs": {
        "summary": "List Runs",
        "description": "Returns a list of runs. Returns empty list if Supabase is not configured.",
    },
    "GET /api/features": {
        "summary": "List Features",
        "description": "Returns features. If run_id is specified, returns only features for that run.",
    },
    "GET /api/settings": {
        "summary": "Get Settings",
        "description": "Returns current settings.",
    },
    "PUT /api/settings": {
        "summary": "Update Settings",
        "description": "Updates settings.",
    },
    "GET /api/runs/{run_id}/spec": {
        "summary": "Get Spec Document",
        "description": "Returns the full spec_markdown for the given run_id.",
    },
    "POST /run/{run_id}/implement": {
        "summary": "Start Implementation",
        "description": "Resumes a run in spec_review state and executes the implementation phase.",
    },
    "GET /dashboard": {
        "summary": "Dashboard",
        "description": "Simple UI: displays run list, details, and next system proposal on a single page.",
    },
    "GET /api/next-system-suggestion": {
        "summary": "Next System Suggestion",
        "description": "Returns the content and update date of data/next_system_suggestion.md. Returns null if file does not exist.",
    },
    # --- Run ---
    "POST /run": {
        "summary": "Run Agent",
        "description": (
            "Accepts a requirement and runs the agent.\n"
            "Auto Execute ON: Runs full pipeline (Spec → Coder → Review → GitHub push).\n"
            "Auto Execute OFF: Runs spec only and pauses at spec_review state "
            "(resume with /run/{run_id}/implement after review on dashboard)."
        ),
    },
    # --- Notion ---
    "POST /webhook/notion": {
        "summary": "Notion Webhook",
        "description": (
            "Receives Notion Webhook events. Returns 200 for verification requests.\n"
            "For events: verifies X-Notion-Signature, checks if entity is a page with "
            "'Implementation Requested' status, then launches agent run in the background."
        ),
    },
    "POST /run-from-database": {
        "summary": "Run from Notion Database",
        "description": (
            "Fetches pages with 'Implementation Requested' status from a Notion database "
            "and processes them sequentially with develop_agent."
        ),
    },
    # --- OAuth: Notion ---
    "GET /api/oauth/notion/authorize": {
        "summary": "Notion OAuth Authorize",
        "description": "Redirects to Notion's OAuth authorization screen.",
    },
    "GET /api/oauth/notion/callback": {
        "summary": "Notion OAuth Callback",
        "description": "Notion OAuth callback: exchanges authorization code for access_token.",
    },
    # --- OAuth: Slack ---
    "GET /api/oauth/slack/authorize": {
        "summary": "Slack OAuth Authorize",
        "description": "Redirects to Slack's OAuth v2 authorization screen.",
    },
    "GET /api/oauth/slack/callback": {
        "summary": "Slack OAuth Callback",
        "description": "Slack OAuth callback: exchanges authorization code for access_token.",
    },
    "POST /webhook/slack": {
        "summary": "Slack Webhook",
        "description": (
            "Slack Events API Webhook.\n"
            "Responds immediately to URL Verification challenges.\n"
            "For message events: returns 200 immediately and processes agent run "
            "in BackgroundTasks (Slack 3-second rule)."
        ),
    },
    # --- Chatwork ---
    "POST /webhook/chatwork": {
        "summary": "Chatwork Webhook",
        "description": "Chatwork Webhook: receives room messages and runs agent in the background.",
    },
    # --- OAuth: Google Drive ---
    "GET /api/oauth/gdrive/authorize": {
        "summary": "Google Drive OAuth Authorize",
        "description": "Redirects to Google OAuth authorization screen (offline access for refresh_token).",
    },
    "GET /api/oauth/gdrive/callback": {
        "summary": "Google Drive OAuth Callback",
        "description": "Google OAuth callback: exchanges code for access_token + refresh_token.",
    },
    "POST /webhook/gdrive": {
        "summary": "Google Drive Webhook",
        "description": 'Google Drive manual trigger: accepts {doc_id: "..."} and runs the agent.',
    },
    "GET /api/gdrive/poll": {
        "summary": "Google Drive Folder Polling",
        "description": "Google Drive folder polling: detects new/updated documents and batch-processes them.",
    },
    # --- Admin ---
    "GET /api/admin/runs": {
        "summary": "Admin: Runs with Details",
        "description": "Returns runs with error logs, token counts, and cost (from state_snapshot).",
    },
    "GET /api/admin/audit-logs": {
        "summary": "Admin: Audit Logs",
        "description": "Returns sandbox audit logs. Filter by run_id optionally.",
    },
    "GET /api/admin/oauth-status": {
        "summary": "Admin: OAuth Status",
        "description": "Returns OAuth connection status for each provider (no tokens exposed).",
    },
    "GET /api/admin/kpi": {
        "summary": "Admin: KPI Summary",
        "description": "Returns aggregated KPIs: success rate, average cost, budget exceeded count, genre breakdown, and alert events.",
    },
    "GET /api/admin/rule-changes": {
        "summary": "Admin: Rule Changes",
        "description": "Returns rule auto-merge history from rule_changes table.",
    },
}


def translate_openapi_schema(schema: dict, lang: str) -> dict:
    """OpenAPI スキーマの description / summary を指定言語に差し替える。
    lang=ja の場合は docstring のまま（無変更）。
    """
    if lang == "ja":
        return schema

    translations = _OPENAPI_EN
    import copy

    schema = copy.deepcopy(schema)

    # アプリタイトル
    if "info" in schema:
        schema["info"]["title"] = "Develop Agent API"
        schema["info"]["description"] = "AI-powered development agent system API"

    paths = schema.get("paths") or {}
    for path, methods in paths.items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            key = f"{method.upper()} {path}"
            tr = translations.get(key)
            if tr:
                if "summary" in tr:
                    operation["summary"] = tr["summary"]
                if "description" in tr:
                    operation["description"] = tr["description"]

    return schema


# =====================================================================
# ダッシュボード翻訳
# =====================================================================

DASHBOARD_STRINGS: dict[str, dict[str, str]] = {
    "ja": {
        "page_title": "Develop Agent - ダッシュボード",
        "app_title": "Develop Agent Dashboard",
        "login_title": "Develop Agent",
        "login_prompt": "メールアドレスを入力してマジックリンクでログインしてください。",
        "login_placeholder": "you@example.com",
        "login_btn": "ログインリンクを送信",
        "login_sending": "送信中...",
        "login_sent": "ログインリンクをメールに送信しました。メールを確認してください。",
        "login_email_required": "メールアドレスを入力してください。",
        "login_error": "エラーが発生しました。",
        "login_config_missing": "サーバーの認証設定がありません。",
        "login_init_failed": "認証の初期化に失敗しました。",
        "logout": "ログアウト",
        "settings": "設定",
        "auto_exec_on": "自動実行: ON（即座にフルパイプラインを実行）",
        "auto_exec_off": "自動実行: OFF（Spec 後に一時停止してレビュー）",
        "settings_error": "設定の読み込みに失敗しました",
        "settings_save_error": "設定の保存に失敗しました",
        "next_proposal": "次のシステム提案",
        "no_proposal": "提案はまだありません。エージェントを実行してデータを蓄積してください。",
        "load_failed": "読み込みに失敗しました。",
        "run_list": "Run 一覧",
        "filter_all": "すべて",
        "filter_spec_review": "Spec レビュー",
        "filter_in_progress": "実行中",
        "filter_published": "完了",
        "filter_failed": "失敗",
        "no_runs": "Run が見つかりません。",
        "runs_load_failed": "Run の読み込みに失敗しました。",
        "detail": "詳細",
        "select_run": "一覧から Run を選択してください。",
        "loading": "読み込み中...",
        "spec_document": "Spec ドキュメント",
        "start_impl": "実装を開始",
        "implementing": "実装中...",
        "summary": "サマリー",
        "generated_files": "生成ファイル",
        "detail_load_failed": "詳細の読み込みに失敗しました。",
        "no_detail": "この Run の詳細はありません。",
        "updated": "更新日時",
        "status_processing": "処理中",
        "status_spec_review": "Spec レビュー",
        "status_implementing": "実装中",
        "status_published": "完了",
        "status_failed": "失敗",
        "lang_label": "言語",
    },
    "en": {
        "page_title": "Develop Agent - Dashboard",
        "app_title": "Develop Agent Dashboard",
        "login_title": "Develop Agent",
        "login_prompt": "Enter your email address to log in with a magic link.",
        "login_placeholder": "you@example.com",
        "login_btn": "Send Login Link",
        "login_sending": "Sending...",
        "login_sent": "Login link sent to your email. Please check your inbox.",
        "login_email_required": "Please enter your email address.",
        "login_error": "An error occurred.",
        "login_config_missing": "Auth configuration missing on server.",
        "login_init_failed": "Failed to initialize authentication.",
        "logout": "Logout",
        "settings": "Settings",
        "auto_exec_on": "Auto Execute: ON (runs full pipeline immediately)",
        "auto_exec_off": "Auto Execute: OFF (pauses after spec for review)",
        "settings_error": "Error loading settings",
        "settings_save_error": "Error saving settings",
        "next_proposal": "Next System Proposal",
        "no_proposal": "No proposals yet. Run an agent to accumulate data.",
        "load_failed": "Failed to load.",
        "run_list": "Run List",
        "filter_all": "All",
        "filter_spec_review": "Spec Review",
        "filter_in_progress": "In Progress",
        "filter_published": "Published",
        "filter_failed": "Failed",
        "no_runs": "No runs found.",
        "runs_load_failed": "Failed to load runs.",
        "detail": "Detail",
        "select_run": "Select a run from the list.",
        "loading": "Loading...",
        "spec_document": "Spec Document",
        "start_impl": "Start Implementation",
        "implementing": "Implementing...",
        "summary": "Summary",
        "generated_files": "Generated Files",
        "detail_load_failed": "Failed to load details.",
        "no_detail": "No details available for this run.",
        "updated": "Updated",
        "status_processing": "Processing",
        "status_spec_review": "Spec Review",
        "status_implementing": "Implementing",
        "status_published": "Published",
        "status_failed": "Failed",
        "lang_label": "Language",
    },
}

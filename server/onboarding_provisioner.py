"""
オンボーディング自動プロビジョニング。
GitHub OAuth / Supabase PAT / Vercel Token を使って
クライアント企業のインフラを自動構築する。
"""

import base64
import logging
import secrets

import httpx

from server import company as company_module
from server import oauth_store

logger = logging.getLogger(__name__)

# ---------- ボイラープレートテンプレート ----------
# onboarding.sh と同一の Next.js テンプレート

_PACKAGE_JSON = """{
  "name": "client-dashboard",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "^14.2.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "@supabase/supabase-js": "^2.45.0"
  },
  "devDependencies": {
    "typescript": "^5.5.0",
    "@types/node": "^20.14.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "eslint": "^8.57.0",
    "eslint-config-next": "^14.2.0"
  }
}"""

_TSCONFIG_JSON = """{
  "compilerOptions": {
    "target": "es5",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}"""

_NEXT_CONFIG_JS = """/** @type {import('next').NextConfig} */
const nextConfig = {}
module.exports = nextConfig"""

_TAILWIND_CONFIG_TS = """import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: { extend: {} },
  plugins: [],
};
export default config;"""

_POSTCSS_CONFIG_JS = """module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}"""

_GITIGNORE = """node_modules/
.next/
out/
.env.local
.vercel
*.tsbuildinfo
next-env.d.ts"""

_SUPABASE_TS = """import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);"""

_GENRES_TS = """export type Genre = {
  id: string;
  title: string;
  icon: string;
  description: string;
};

export const genres: Genre[] = [
  { id: "sfa",        title: "SFA/\\u55b6\\u696dエージェント",       icon: "\\ud83d\\udcca", description: "商談管理・パイプライン・見積書を一元管理" },
  { id: "crm",        title: "CRMエージェント",            icon: "\\ud83d\\udc65", description: "顧客情報・関係履歴・フォローアップを管理" },
  { id: "accounting", title: "会計エージェント",            icon: "\\ud83d\\udcb4", description: "請求・仕訳・財務分析を自動化" },
  { id: "legal",      title: "法務エージェント",            icon: "\\u2696\\ufe0f", description: "契約書・稟議・コンプライアンスを管理" },
  { id: "admin",      title: "事務エージェント",            icon: "\\ud83d\\udcdd", description: "日報・経費・勤怠・申請業務を効率化" },
  { id: "it",         title: "情シスエージェント",          icon: "\\ud83d\\udda5\\ufe0f", description: "IT資産・ヘルプデスク・インフラを一元管理" },
  { id: "marketing",  title: "マーケティングエージェント",  icon: "\\ud83d\\udce3", description: "集客・広告・施策効果を可視化" },
  { id: "design",     title: "デザインエージェント",        icon: "\\ud83c\\udfa8", description: "UI/UX・制作物・デザインシステムを管理" },
  { id: "ma",         title: "M&Aエージェント",             icon: "\\ud83c\\udfe2", description: "買収候補・DD・企業価値分析を支援" },
  { id: "no2",        title: "No.2/経営エージェント",       icon: "\\ud83e\\udde0", description: "KPI・経営分析・戦略提言を提供" },
];"""

_GLOBALS_CSS = """@tailwind base;
@tailwind components;
@tailwind utilities;"""

_SIDEBAR_TSX = """"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { genres } from "@/lib/genres";

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 min-h-screen bg-slate-900 text-white flex flex-col">
      <div className="p-4 border-b border-slate-700">
        <h1 className="text-lg font-bold">
          {process.env.NEXT_PUBLIC_COMPANY_NAME || "Dashboard"}
        </h1>
      </div>
      <nav className="flex-1 py-2">
        <Link
          href="/"
          className={`block px-4 py-2 text-sm hover:bg-slate-800 ${
            pathname === "/" ? "bg-slate-800 font-bold" : ""
          }`}
        >
          🏠 ホーム
        </Link>
        {genres.map((g) => (
          <Link
            key={g.id}
            href={`/${g.id}`}
            className={`block px-4 py-2 text-sm hover:bg-slate-800 ${
              pathname === `/${g.id}` ? "bg-slate-800 font-bold" : ""
            }`}
          >
            {g.icon} {g.title}
          </Link>
        ))}
      </nav>
    </aside>
  );
}"""

_LAYOUT_TSX = """import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";

export const metadata: Metadata = {
  title: "AI社員 ダッシュボード",
  description: "AI社員が構築した業務システム",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body className="flex">
        <Sidebar />
        <main className="flex-1 min-h-screen bg-gray-50 p-6">
          {children}
        </main>
      </body>
    </html>
  );
}"""

_GENRECARD_TSX = """import Link from "next/link";
import type { Genre } from "@/lib/genres";

export default function GenreCard({ genre }: { genre: Genre }) {
  return (
    <Link
      href={`/${genre.id}`}
      className="block p-6 bg-white rounded-lg border border-gray-200 hover:shadow-md transition-shadow"
    >
      <div className="text-3xl mb-3">{genre.icon}</div>
      <h3 className="font-bold text-gray-900 mb-1">{genre.title}システム</h3>
      <p className="text-sm text-gray-500">{genre.description}</p>
    </Link>
  );
}"""

_HOME_TSX = """import GenreCard from "@/components/home/GenreCard";
import { genres } from "@/lib/genres";

export default function Home() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">
        ダッシュボード
      </h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {genres.map((genre) => (
          <GenreCard key={genre.id} genre={genre} />
        ))}
      </div>
    </div>
  );
}"""

_GENRE_PAGE_TSX = """import { genres } from "@/lib/genres";
import { notFound } from "next/navigation";
import Link from "next/link";

export default function GenrePage({ params }: { params: { genre: string } }) {
  const genre = genres.find((g) => g.id === params.genre);
  if (!genre) return notFound();

  return (
    <div>
      <nav className="text-sm text-gray-500 mb-4">
        <Link href="/" className="hover:underline">ホーム</Link>
        <span className="mx-2">/</span>
        <span>{genre.title}</span>
      </nav>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">
        {genre.icon} {genre.title}システム
      </h1>
      <p className="text-gray-500 mb-8">{genre.description}</p>
      <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-400">
        AI社員がこのジャンルのシステムを構築すると、ここに表示されます。
      </div>
    </div>
  );
}"""

# クライアント用 Supabase 初期テーブル SQL
_CLIENT_SUPABASE_SQL = """
CREATE TABLE IF NOT EXISTS agent_outputs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  genre TEXT NOT NULL,
  output_type TEXT,
  title TEXT,
  content JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE agent_outputs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE policyname = 'agent_outputs_select'
  ) THEN
    CREATE POLICY "agent_outputs_select" ON agent_outputs
      FOR SELECT USING (company_id = current_setting('app.company_id', true));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE policyname = 'agent_outputs_insert'
  ) THEN
    CREATE POLICY "agent_outputs_insert" ON agent_outputs
      FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true));
  END IF;
END
$$;
"""


async def _run_sql_on_project(
    client: httpx.AsyncClient,
    project_ref: str,
    sql: str,
    mgmt_token: str = "",
    service_role_key: str = "",
) -> bool:
    """
    Supabase プロジェクトで SQL を実行する。3 つの方法を順に試す:
    1. pg-meta API (service_role キー使用)
    2. Management API /database/migrations
    3. Management API /database/query (レガシー)
    """
    # 方法 1: pg-meta API（最も確実）
    if service_role_key:
        try:
            resp = await client.post(
                f"https://{project_ref}.supabase.co/pg/query",
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": "application/json",
                },
                json={"query": sql},
            )
            if resp.status_code in (200, 201):
                logger.info("SQL executed via pg-meta API")
                return True
            logger.info("pg-meta failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.info("pg-meta exception: %s", e)

    # 方法 2: Management API /database/migrations
    if mgmt_token:
        mgmt_headers = {
            "Authorization": f"Bearer {mgmt_token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await client.post(
                f"{_SUPABASE_MGMT_API}/v1/projects/{project_ref}/database/migrations",
                headers=mgmt_headers,
                json={"query": sql},
            )
            if resp.status_code in (200, 201):
                logger.info("SQL executed via migrations API")
                return True
            logger.info("migrations API failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.info("migrations API exception: %s", e)

    # 方法 3: レガシー /database/query
    if mgmt_token:
        try:
            resp = await client.post(
                f"{_SUPABASE_MGMT_API}/v1/projects/{project_ref}/database/query",
                headers={
                    "Authorization": f"Bearer {mgmt_token}",
                    "Content-Type": "application/json",
                },
                json={"query": sql},
            )
            if resp.status_code == 200:
                logger.info("SQL executed via query API")
                return True
            logger.info("query API failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.info("query API exception: %s", e)

    return False


def _env_local_example(company_name: str, repo_name: str) -> str:
    return f"""# Supabase（クライアント用プロジェクト）
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJxxx

# アプリ設定
NEXT_PUBLIC_COMPANY_NAME={company_name}
NEXT_PUBLIC_APP_URL=https://{repo_name}.vercel.app"""


def _boilerplate_files(company_name: str, repo_name: str) -> dict[str, str]:
    """リポジトリに push するファイル一覧を返す。{path: content}"""
    return {
        "package.json": _PACKAGE_JSON,
        "tsconfig.json": _TSCONFIG_JSON,
        "next.config.js": _NEXT_CONFIG_JS,
        "tailwind.config.ts": _TAILWIND_CONFIG_TS,
        "postcss.config.js": _POSTCSS_CONFIG_JS,
        ".gitignore": _GITIGNORE,
        ".env.local.example": _env_local_example(company_name, repo_name),
        "src/lib/supabase.ts": _SUPABASE_TS,
        "src/lib/genres.ts": _GENRES_TS,
        "src/app/globals.css": _GLOBALS_CSS,
        "src/components/layout/Sidebar.tsx": _SIDEBAR_TSX,
        "src/app/layout.tsx": _LAYOUT_TSX,
        "src/components/home/GenreCard.tsx": _GENRECARD_TSX,
        "src/app/page.tsx": _HOME_TSX,
        "src/app/[genre]/page.tsx": _GENRE_PAGE_TSX,
    }


# ---------- GitHub プロビジョニング ----------

_GITHUB_API = "https://api.github.com"


async def provision_github(
    company_id: str,
    access_token: str,
    company_slug: str,
    company_name: str = "",
) -> dict:
    """
    GitHub リポジトリを作成し、Next.js ボイラープレートを push する。

    Returns: {"ok": True, "repo": "owner/repo"} or {"ok": False, "error": "..."}
    """
    repo_name = f"develop_agent-{company_slug}"
    company_name = company_name or company_slug
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        # 1. 認証ユーザーを取得
        user_resp = await client.get(f"{_GITHUB_API}/user", headers=headers)
        if user_resp.status_code != 200:
            return {"ok": False, "error": f"GitHub API auth failed: {user_resp.status_code}"}
        owner = user_resp.json()["login"]
        repo_full = f"{owner}/{repo_name}"

        # 2. リポジトリ作成（既存ならスキップ）
        check_resp = await client.get(f"{_GITHUB_API}/repos/{repo_full}", headers=headers)
        if check_resp.status_code == 404:
            create_resp = await client.post(
                f"{_GITHUB_API}/user/repos",
                headers=headers,
                json={
                    "name": repo_name,
                    "private": True,
                    "auto_init": True,
                    "description": f"AI社員 ダッシュボード - {company_name}",
                },
            )
            if create_resp.status_code not in (200, 201):
                return {"ok": False, "error": f"Repo create failed: {create_resp.text}"}
            logger.info("Created repo %s", repo_full)
        elif check_resp.status_code == 200:
            logger.info("Repo %s already exists, skipping creation", repo_full)
        else:
            return {"ok": False, "error": f"Repo check failed: {check_resp.status_code}"}

        # 3. ボイラープレートファイルを push（Contents API で1ファイルずつ）
        files = _boilerplate_files(company_name, repo_name)
        for path, content in files.items():
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            put_resp = await client.put(
                f"{_GITHUB_API}/repos/{repo_full}/contents/{path}",
                headers=headers,
                json={
                    "message": f"Add {path}",
                    "content": encoded,
                },
            )
            if put_resp.status_code == 422:
                # ファイルが既に存在する場合は SHA を取得して更新
                get_resp = await client.get(
                    f"{_GITHUB_API}/repos/{repo_full}/contents/{path}",
                    headers=headers,
                )
                if get_resp.status_code == 200:
                    sha = get_resp.json().get("sha", "")
                    update_resp = await client.put(
                        f"{_GITHUB_API}/repos/{repo_full}/contents/{path}",
                        headers=headers,
                        json={
                            "message": f"Update {path}",
                            "content": encoded,
                            "sha": sha,
                        },
                    )
                    if update_resp.status_code in (200, 201):
                        logger.info("Updated %s in %s", path, repo_full)
                    else:
                        logger.warning("Failed to update %s: %s", path, update_resp.status_code)
                else:
                    logger.info("File %s exists but could not fetch SHA, skipping", path)
            elif put_resp.status_code not in (200, 201):
                logger.warning("Failed to push %s: %s", path, put_resp.status_code)

    # 4. トークン保存
    oauth_store.save_token(
        provider="github",
        tenant_id=company_id,
        access_token=access_token,
    )

    # 5. インフラ設定更新
    company_module.update_company_infra(company_id, {
        "github_repository": repo_full,
        "github_token_secret_name": f"github-token-{company_slug}",
    })

    # 6. オンボーディングステップ更新
    company_module.update_onboarding(company_id, {
        "github_repo": True,
        "github_initial_commit": True,
        "env_github_repository": True,
    })

    return {"ok": True, "repo": repo_full}


# ---------- Supabase プロビジョニング ----------

_SUPABASE_MGMT_API = "https://api.supabase.com"


async def provision_supabase(
    company_id: str,
    access_token: str,
    company_slug: str,
) -> dict:
    """
    Supabase Management API でプロジェクトを作成し、テーブルを初期化する。

    Returns: {"ok": True, "url": "https://xxx.supabase.co", "anon_key": "..."} or {"ok": False, "error": "..."}
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    db_pass = secrets.token_urlsafe(24)

    async with httpx.AsyncClient(timeout=120) as client:
        # 1. 組織一覧取得
        org_resp = await client.get(f"{_SUPABASE_MGMT_API}/v1/organizations", headers=headers)
        if org_resp.status_code != 200:
            return {"ok": False, "error": f"Failed to get organizations: {org_resp.status_code} {org_resp.text}"}
        orgs = org_resp.json()
        if not orgs:
            return {"ok": False, "error": "No Supabase organization found. Create one first."}
        org_id = orgs[0]["id"]

        # 2. プロジェクト作成
        project_name = f"develop_agent-{company_slug}"
        create_resp = await client.post(
            f"{_SUPABASE_MGMT_API}/v1/projects",
            headers=headers,
            json={
                "name": project_name,
                "organization_id": org_id,
                "region": "ap-northeast-1",
                "db_pass": db_pass,
                "plan": "free",
            },
        )
        if create_resp.status_code not in (200, 201):
            return {"ok": False, "error": f"Project create failed: {create_resp.text}"}

        project = create_resp.json()
        project_ref = project.get("id", "")
        if not project_ref:
            return {"ok": False, "error": "No project ref in response"}

        # 3. プロジェクト準備待ち（最大120秒）
        import asyncio

        supabase_url = ""
        anon_key = ""
        for _ in range(24):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"{_SUPABASE_MGMT_API}/v1/projects/{project_ref}",
                headers=headers,
            )
            if status_resp.status_code == 200:
                proj_data = status_resp.json()
                status = proj_data.get("status", "")
                if status == "ACTIVE_HEALTHY":
                    supabase_url = f"https://{project_ref}.supabase.co"
                    break

        if not supabase_url:
            return {"ok": False, "error": "Project did not become ready within 120 seconds"}

        # 4. API キー取得（anon + service_role）
        service_role_key = ""
        keys_resp = await client.get(
            f"{_SUPABASE_MGMT_API}/v1/projects/{project_ref}/api-keys",
            headers=headers,
        )
        if keys_resp.status_code == 200:
            for key_data in keys_resp.json():
                if key_data.get("name") == "anon":
                    anon_key = key_data.get("api_key", "")
                elif key_data.get("name") == "service_role":
                    service_role_key = key_data.get("api_key", "")

        # 5. テーブル初期化（DB 接続安定待ちのためリトライ）
        tables_ok = False
        for attempt in range(5):
            tables_ok = await _run_sql_on_project(
                client, project_ref, _CLIENT_SUPABASE_SQL,
                mgmt_token=access_token, service_role_key=service_role_key,
            )
            if tables_ok:
                break
            logger.info("Table init attempt %d failed", attempt + 1)
            await asyncio.sleep(5)
        if not tables_ok:
            logger.warning("Table init failed after 5 attempts for project %s", project_ref)

    # 6. トークン保存（oauth_store + 暗号化カラム）
    oauth_store.save_token(
        provider="supabase",
        tenant_id=company_id,
        access_token=access_token,
    )
    company_module.save_infra_token(company_id, "supabase_mgmt_token_enc", access_token)

    # 7. インフラ設定更新
    company_module.update_company_infra(company_id, {
        "client_supabase_url": supabase_url,
    })

    # 8. オンボーディングステップ更新
    ob_updates = {"supabase_project": True}
    if tables_ok:
        ob_updates["supabase_tables"] = True
    company_module.update_onboarding(company_id, ob_updates)

    return {
        "ok": True,
        "url": supabase_url,
        "anon_key": anon_key,
        "tables_initialized": tables_ok,
    }


async def retry_supabase_tables(
    company_id: str,
    access_token: str,
) -> dict:
    """既存の Supabase プロジェクトに対してテーブル初期化をリトライする。"""
    import asyncio

    # company_infra から supabase_url を取得してプロジェクト ref を割り出す
    infra = company_module.get_company_infra(company_id) or {}
    supabase_url = infra.get("client_supabase_url", "")
    if not supabase_url:
        return {"ok": False, "error": "No Supabase project URL found"}

    # https://xxxxx.supabase.co → xxxxx
    project_ref = supabase_url.replace("https://", "").replace(".supabase.co", "").strip("/")
    if not project_ref:
        return {"ok": False, "error": "Could not parse project ref from URL"}

    # service_role キーを Management API から取得
    service_role_key = ""
    async with httpx.AsyncClient(timeout=120) as client:
        keys_resp = await client.get(
            f"{_SUPABASE_MGMT_API}/v1/projects/{project_ref}/api-keys",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if keys_resp.status_code == 200:
            for key_data in keys_resp.json():
                if key_data.get("name") == "service_role":
                    service_role_key = key_data.get("api_key", "")
                    break

        tables_ok = False
        for attempt in range(5):
            tables_ok = await _run_sql_on_project(
                client, project_ref, _CLIENT_SUPABASE_SQL,
                mgmt_token=access_token, service_role_key=service_role_key,
            )
            if tables_ok:
                break
            logger.info("Table retry attempt %d failed", attempt + 1)
            await asyncio.sleep(5)

    if tables_ok:
        company_module.update_onboarding(company_id, {"supabase_tables": True})

    return {
        "ok": tables_ok,
        "tables_initialized": tables_ok,
        "error": "" if tables_ok else f"Table init failed: {last_error}",
    }


# ---------- Vercel プロビジョニング ----------

_VERCEL_API = "https://api.vercel.com"


async def provision_vercel(
    company_id: str,
    access_token: str,
    company_slug: str,
    github_repo: str,
    supabase_url: str = "",
    supabase_anon_key: str = "",
) -> dict:
    """
    Vercel API でプロジェクトを作成し、GitHub リポと接続、環境変数を設定する。

    Returns: {"ok": True, "url": "https://xxx.vercel.app"} or {"ok": False, "error": "..."}
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    project_name = f"develop_agent-{company_slug}"

    async with httpx.AsyncClient(timeout=60) as client:
        # 1. プロジェクト作成（GitHub リポ連携）
        create_body: dict = {
            "name": project_name,
            "framework": "nextjs",
        }
        if github_repo:
            create_body["gitRepository"] = {
                "type": "github",
                "repo": github_repo,
            }

        create_resp = await client.post(
            f"{_VERCEL_API}/v10/projects",
            headers=headers,
            json=create_body,
        )
        if create_resp.status_code not in (200, 201):
            # プロジェクトが既に存在する場合
            if create_resp.status_code == 409:
                logger.info("Vercel project %s already exists", project_name)
                # 既存プロジェクトを取得
                get_resp = await client.get(
                    f"{_VERCEL_API}/v9/projects/{project_name}",
                    headers=headers,
                )
                if get_resp.status_code != 200:
                    return {"ok": False, "error": f"Failed to get existing project: {get_resp.text}"}
                project_data = get_resp.json()
            else:
                return {"ok": False, "error": f"Project create failed: {create_resp.text}"}
        else:
            project_data = create_resp.json()

        project_id = project_data.get("id", "")

        # 2. 環境変数設定
        env_vars = [
            {"key": "NEXT_PUBLIC_COMPANY_NAME", "value": company_slug, "target": ["production", "preview", "development"], "type": "plain"},
        ]
        if supabase_url:
            env_vars.append(
                {"key": "NEXT_PUBLIC_SUPABASE_URL", "value": supabase_url, "target": ["production", "preview", "development"], "type": "plain"}
            )
        if supabase_anon_key:
            env_vars.append(
                {"key": "NEXT_PUBLIC_SUPABASE_ANON_KEY", "value": supabase_anon_key, "target": ["production", "preview", "development"], "type": "plain"}
            )

        if env_vars and project_id:
            env_resp = await client.post(
                f"{_VERCEL_API}/v10/projects/{project_id}/env",
                headers=headers,
                json=env_vars,
            )
            if env_resp.status_code not in (200, 201):
                logger.warning("Env vars set failed: %s", env_resp.text)

        # 3. 初回デプロイをトリガー（GitHub リポ連携時）
        if github_repo and project_id:
            deploy_triggered = False
            # 方法 1: Deploy Hook を作成して呼び出す（最も確実）
            try:
                hook_resp = await client.post(
                    f"{_VERCEL_API}/v1/projects/{project_id}/deploy-hooks",
                    headers=headers,
                    json={"name": "auto-deploy", "ref": "main"},
                )
                if hook_resp.status_code in (200, 201):
                    hook_url = hook_resp.json().get("url", "")
                    if hook_url:
                        trigger_resp = await client.post(hook_url)
                        if trigger_resp.status_code in (200, 201):
                            logger.info("Vercel deployment triggered via deploy hook")
                            deploy_triggered = True
                        else:
                            logger.warning("Deploy hook trigger failed: %s", trigger_resp.status_code)
                else:
                    logger.info("Deploy hook creation failed: %s %s", hook_resp.status_code, hook_resp.text[:200])
            except Exception as e:
                logger.warning("Deploy hook approach failed: %s", e)

            # 方法 2: gitSource API（フォールバック）
            if not deploy_triggered:
                parts = github_repo.split("/", 1)
                if len(parts) == 2:
                    git_org, git_repo = parts
                    deploy_body = {
                        "name": project_name,
                        "project": project_id,
                        "target": "production",
                        "gitSource": {
                            "type": "github",
                            "org": git_org,
                            "repo": git_repo,
                            "ref": "main",
                        },
                    }
                    deploy_resp = await client.post(
                        f"{_VERCEL_API}/v13/deployments",
                        headers=headers,
                        json=deploy_body,
                    )
                    if deploy_resp.status_code in (200, 201):
                        deploy_data = deploy_resp.json()
                        deploy_url = deploy_data.get("url", "")
                        if deploy_url and not deploy_url.startswith("http"):
                            deploy_url = f"https://{deploy_url}"
                        logger.info("Vercel deployment triggered via API: %s", deploy_url)
                        deploy_triggered = True
                    else:
                        logger.warning("Vercel deployment API failed: %s", deploy_resp.text[:300])

            if not deploy_triggered:
                logger.warning("Could not trigger initial Vercel deployment for %s", project_name)

        # 4. デプロイ URL を取得
        # Vercel はドメインからアンダースコアを除去するため、
        # 最新デプロイの実 URL か alias から取得する
        vercel_url = ""
        aliases = project_data.get("alias", [])
        if aliases:
            vercel_url = f"https://{aliases[0]}"
        else:
            # 最新デプロイから実際のドメインを取得
            try:
                list_resp = await client.get(
                    f"{_VERCEL_API}/v6/deployments",
                    headers=headers,
                    params={"projectId": project_id, "limit": "1", "target": "production"},
                )
                if list_resp.status_code == 200:
                    deploys = list_resp.json().get("deployments", [])
                    if deploys:
                        deploy_url_raw = deploys[0].get("url", "")
                        if deploy_url_raw:
                            vercel_url = f"https://{deploy_url_raw}"
            except Exception:
                pass
            # フォールバック: プロジェクト名からアンダースコアを除去
            if not vercel_url and project_data.get("name"):
                domain = project_data["name"].replace("_", "")
                vercel_url = f"https://{domain}.vercel.app"

    # 5. トークン保存（oauth_store + 暗号化カラム）
    oauth_store.save_token(
        provider="vercel",
        tenant_id=company_id,
        access_token=access_token,
    )
    company_module.save_infra_token(company_id, "vercel_token_enc", access_token)

    # 6. インフラ設定更新
    if vercel_url:
        company_module.update_company_infra(company_id, {
            "vercel_project_url": vercel_url,
        })

    # 7. オンボーディングステップ更新
    ob_updates = {"vercel_project": True}
    if supabase_url or supabase_anon_key:
        ob_updates["vercel_env"] = True
    company_module.update_onboarding(company_id, ob_updates)

    return {"ok": True, "url": vercel_url}

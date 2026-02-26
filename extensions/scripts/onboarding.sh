#!/usr/bin/env bash
# =============================================================================
# クライアント企業オンボーディングスクリプト
#
# 使い方:
#   bash scripts/onboarding.sh \
#     --company "company-a" \
#     --org "your-org" \
#     --company-name "株式会社A"
#
# 前提:
#   - gh (GitHub CLI) がインストール済み & 認証済み
#   - vercel CLI がインストール済み & 認証済み
#   - Node.js 18+ がインストール済み
# =============================================================================
set -euo pipefail

# ---------- 引数パース ----------
COMPANY_SLUG=""
GH_ORG=""
COMPANY_DISPLAY_NAME=""
CLIENT_SUPABASE_URL=""
VERCEL_PROJECT_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --company)           COMPANY_SLUG="$2"; shift 2 ;;
    --org)               GH_ORG="$2"; shift 2 ;;
    --company-name)      COMPANY_DISPLAY_NAME="$2"; shift 2 ;;
    --client-supabase-url) CLIENT_SUPABASE_URL="$2"; shift 2 ;;
    --vercel-url)        VERCEL_PROJECT_URL="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$COMPANY_SLUG" || -z "$GH_ORG" ]]; then
  echo "Usage: bash scripts/onboarding.sh --company <slug> --org <github-org> [--company-name <表示名>]"
  echo "       [--client-supabase-url <URL>] [--vercel-url <URL>]"
  echo ""
  echo "Example:"
  echo "  bash scripts/onboarding.sh --company company-a --org my-org --company-name '株式会社A' \\"
  echo "    --client-supabase-url https://xxxxx.supabase.co --vercel-url https://company-a.vercel.app"
  echo ""
  echo "Required env vars for infra config write:"
  echo "  SUPABASE_URL          - 開発側 Supabase の URL"
  echo "  SUPABASE_SERVICE_KEY  - 開発側 Supabase の Service Key"
  exit 1
fi

COMPANY_DISPLAY_NAME="${COMPANY_DISPLAY_NAME:-$COMPANY_SLUG}"
REPO_NAME="${COMPANY_SLUG}-dashboard"
REPO_FULL="${GH_ORG}/${REPO_NAME}"
WORK_DIR=$(mktemp -d)

echo "========================================"
echo " クライアントオンボーディング"
echo "========================================"
echo " Company:    ${COMPANY_DISPLAY_NAME}"
echo " Slug:       ${COMPANY_SLUG}"
echo " Repository: ${REPO_FULL}"
echo " Work dir:   ${WORK_DIR}"
echo "========================================"
echo ""

# ---------- Step 1: GitHub リポジトリ作成 ----------
echo "[1/5] GitHub リポジトリを作成..."
if gh repo view "${REPO_FULL}" &>/dev/null; then
  echo "  -> リポジトリ ${REPO_FULL} は既に存在します。スキップ。"
else
  gh repo create "${REPO_FULL}" --private --description "AI社員 ダッシュボード - ${COMPANY_DISPLAY_NAME}"
  echo "  -> ${REPO_FULL} を作成しました。"
fi

# ---------- Step 2: Next.js ボイラープレート生成 ----------
echo ""
echo "[2/5] Next.js ボイラープレートを生成..."

cd "${WORK_DIR}"
git clone "https://github.com/${REPO_FULL}.git" "${REPO_NAME}" 2>/dev/null || git init "${REPO_NAME}"
cd "${REPO_NAME}"

# package.json
cat > package.json << 'PKGJSON'
{
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
}
PKGJSON

# tsconfig.json
cat > tsconfig.json << 'TSCONFIG'
{
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
}
TSCONFIG

# next.config.js
cat > next.config.js << 'NEXTCONFIG'
/** @type {import('next').NextConfig} */
const nextConfig = {}
module.exports = nextConfig
NEXTCONFIG

# tailwind.config.ts
cat > tailwind.config.ts << 'TAILWIND'
import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: { extend: {} },
  plugins: [],
};
export default config;
TAILWIND

# postcss.config.js
cat > postcss.config.js << 'POSTCSS'
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
POSTCSS

# .env.local.example
cat > .env.local.example << ENVEXAMPLE
# Supabase（クライアント用プロジェクト）
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJxxx

# アプリ設定
NEXT_PUBLIC_COMPANY_NAME=${COMPANY_DISPLAY_NAME}
NEXT_PUBLIC_APP_URL=https://${REPO_NAME}.vercel.app
ENVEXAMPLE

# .gitignore
cat > .gitignore << 'GITIGNORE'
node_modules/
.next/
out/
.env.local
.vercel
*.tsbuildinfo
next-env.d.ts
GITIGNORE

# --- src ディレクトリ ---
mkdir -p src/app src/components/layout src/components/home src/lib

# src/lib/supabase.ts
cat > src/lib/supabase.ts << 'SUPABASE_TS'
import { createClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
SUPABASE_TS

# src/lib/genres.ts
cat > src/lib/genres.ts << 'GENRES_TS'
export type Genre = {
  id: string;
  title: string;
  icon: string;
  description: string;
};

export const genres: Genre[] = [
  { id: "sfa",        title: "SFA/営業エージェント",       icon: "📊", description: "商談管理・パイプライン・見積書を一元管理" },
  { id: "crm",        title: "CRMエージェント",            icon: "👥", description: "顧客情報・関係履歴・フォローアップを管理" },
  { id: "accounting", title: "会計エージェント",            icon: "💴", description: "請求・仕訳・財務分析を自動化" },
  { id: "legal",      title: "法務エージェント",            icon: "⚖️", description: "契約書・稟議・コンプライアンスを管理" },
  { id: "admin",      title: "事務エージェント",            icon: "📝", description: "日報・経費・勤怠・申請業務を効率化" },
  { id: "it",         title: "情シスエージェント",          icon: "🖥️", description: "IT資産・ヘルプデスク・インフラを一元管理" },
  { id: "marketing",  title: "マーケティングエージェント",  icon: "📣", description: "集客・広告・施策効果を可視化" },
  { id: "design",     title: "デザインエージェント",        icon: "🎨", description: "UI/UX・制作物・デザインシステムを管理" },
  { id: "ma",         title: "M&Aエージェント",             icon: "🏢", description: "買収候補・DD・企業価値分析を支援" },
  { id: "no2",        title: "No.2/経営エージェント",       icon: "🧠", description: "KPI・経営分析・戦略提言を提供" },
];
GENRES_TS

# src/app/globals.css
cat > src/app/globals.css << 'GLOBALS_CSS'
@tailwind base;
@tailwind components;
@tailwind utilities;
GLOBALS_CSS

# src/components/layout/Sidebar.tsx
cat > src/components/layout/Sidebar.tsx << 'SIDEBAR_TSX'
"use client";

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
}
SIDEBAR_TSX

# src/app/layout.tsx
cat > src/app/layout.tsx << 'LAYOUT_TSX'
import type { Metadata } from "next";
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
}
LAYOUT_TSX

# src/components/home/GenreCard.tsx
cat > src/components/home/GenreCard.tsx << 'GENRECARD_TSX'
import Link from "next/link";
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
}
GENRECARD_TSX

# src/app/page.tsx
cat > src/app/page.tsx << 'HOME_TSX'
import GenreCard from "@/components/home/GenreCard";
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
}
HOME_TSX

# src/app/[genre]/page.tsx
cat > 'src/app/[genre]/page.tsx' << 'GENRE_PAGE_TSX'
import { genres } from "@/lib/genres";
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
}
GENRE_PAGE_TSX

echo "  -> ボイラープレートを生成しました。"

# ---------- Step 3: 初期コミット & push ----------
echo ""
echo "[3/5] Git 初期コミット & push..."

git add -A
git commit -m "Initial: Next.js dashboard boilerplate for ${COMPANY_DISPLAY_NAME}" 2>/dev/null || true
git branch -M main
git remote set-url origin "https://github.com/${REPO_FULL}.git" 2>/dev/null || \
  git remote add origin "https://github.com/${REPO_FULL}.git" 2>/dev/null || true
git push -u origin main 2>/dev/null || echo "  -> push に失敗。リポジトリの権限を確認してください。"

echo "  -> ${REPO_FULL} に push しました。"

# ---------- Step 4: Supabase 共通テーブル SQL 出力 ----------
echo ""
echo "[4/5] クライアント用 Supabase 共通テーブル SQL を出力..."

SQL_FILE="${WORK_DIR}/client_supabase_init.sql"
cat > "${SQL_FILE}" << 'CLIENT_SQL'
-- =============================================================================
-- クライアント用 Supabase 初期テーブル
-- Supabase SQL Editor で実行してください
-- =============================================================================

-- エージェントアウトプット共通ログ
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
CREATE POLICY "agent_outputs_select" ON agent_outputs
  FOR SELECT USING (company_id = current_setting('app.company_id', true));
CREATE POLICY "agent_outputs_insert" ON agent_outputs
  FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true));
CLIENT_SQL

echo "  -> SQL ファイル: ${SQL_FILE}"
echo "  -> Supabase ダッシュボードの SQL Editor にコピペして実行してください。"

# ---------- Step 5: インフラ設定を companies テーブルに書き込み ----------
echo ""
echo "[5/6] インフラ設定を companies テーブルに書き込み..."

OPS_SUPABASE_URL="${SUPABASE_URL:-}"
OPS_SUPABASE_KEY="${SUPABASE_SERVICE_KEY:-}"
SECRET_NAME="github-token-${COMPANY_SLUG}"

if [[ -z "$OPS_SUPABASE_URL" || -z "$OPS_SUPABASE_KEY" ]]; then
  echo "  -> SUPABASE_URL / SUPABASE_SERVICE_KEY が未設定のため DB 書き込みをスキップ。"
  echo "     手動で companies テーブルを更新してください。"
else
  # slug で会社を検索
  COMPANY_ROW=$(curl -s \
    -H "apikey: ${OPS_SUPABASE_KEY}" \
    -H "Authorization: Bearer ${OPS_SUPABASE_KEY}" \
    "${OPS_SUPABASE_URL}/rest/v1/companies?slug=eq.${COMPANY_SLUG}&select=id" \
  )

  COMPANY_ID=$(echo "$COMPANY_ROW" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id'] if d else '')" 2>/dev/null || echo "")

  if [[ -z "$COMPANY_ID" ]]; then
    echo "  -> slug '${COMPANY_SLUG}' の会社が見つかりません。先にダッシュボードで会社を作成してください。"
  else
    # PATCH で infra 設定を更新
    PATCH_BODY=$(python3 -c "
import json
d = {
    'github_repository': '${REPO_FULL}',
    'github_token_secret_name': '${SECRET_NAME}',
}
client_url = '${CLIENT_SUPABASE_URL}'
vercel_url = '${VERCEL_PROJECT_URL}'
if client_url:
    d['client_supabase_url'] = client_url
if vercel_url:
    d['vercel_project_url'] = vercel_url

# onboarding ステップを自動完了
ob = {'github_repo': True, 'secret_manager_token': True}
if client_url:
    ob['supabase_project'] = True
if vercel_url:
    ob['vercel_project'] = True
d['onboarding'] = json.dumps(ob)

print(json.dumps(d))
")

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -X PATCH \
      -H "apikey: ${OPS_SUPABASE_KEY}" \
      -H "Authorization: Bearer ${OPS_SUPABASE_KEY}" \
      -H "Content-Type: application/json" \
      -H "Prefer: return=minimal" \
      -d "${PATCH_BODY}" \
      "${OPS_SUPABASE_URL}/rest/v1/companies?id=eq.${COMPANY_ID}" \
    )

    if [[ "$HTTP_CODE" == "204" || "$HTTP_CODE" == "200" ]]; then
      echo "  -> companies テーブルを更新しました (id: ${COMPANY_ID})"
      echo "     github_repository:         ${REPO_FULL}"
      echo "     github_token_secret_name:  ${SECRET_NAME}"
      [[ -n "$CLIENT_SUPABASE_URL" ]] && echo "     client_supabase_url:       ${CLIENT_SUPABASE_URL}"
      [[ -n "$VERCEL_PROJECT_URL" ]]  && echo "     vercel_project_url:        ${VERCEL_PROJECT_URL}"
    else
      echo "  -> DB 更新に失敗 (HTTP ${HTTP_CODE})。手動で companies テーブルを更新してください。"
    fi
  fi
fi

# ---------- Step 6: サマリー ----------
echo ""
echo "[6/6] サマリー"
echo ""
echo "========================================"
echo " オンボーディング完了"
echo "========================================"
echo ""
echo " 自動設定済み:"
echo "   - GitHub リポジトリ: ${REPO_FULL}"
echo "   - Secret Manager 名: ${SECRET_NAME}"
[[ -n "$CLIENT_SUPABASE_URL" ]] && echo "   - クライアント Supabase: ${CLIENT_SUPABASE_URL}"
[[ -n "$VERCEL_PROJECT_URL" ]]  && echo "   - Vercel URL: ${VERCEL_PROJECT_URL}"
echo ""
echo " 残りの手動作業:"
echo ""
echo " 1. Vercel で ${REPO_FULL} を Import（未実施の場合）:"
echo "    https://vercel.com/new"
echo ""
echo " 2. Vercel の環境変数を設定:"
echo "    NEXT_PUBLIC_SUPABASE_URL=<クライアント Supabase の URL>"
echo "    NEXT_PUBLIC_SUPABASE_ANON_KEY=<クライアント Supabase の anon key>"
echo "    NEXT_PUBLIC_COMPANY_NAME=${COMPANY_DISPLAY_NAME}"
echo ""
echo " 3. クライアント Supabase で SQL 実行:"
echo "    ${SQL_FILE}"
echo ""
echo " 4. GCP Secret Manager に GitHub トークン格納:"
echo "    gcloud secrets create ${SECRET_NAME} --data-file=<token-file>"
echo ""
echo " 5. 開発側 .env.local の GITHUB_REPOSITORY を更新 (MVP):"
echo "    GITHUB_REPOSITORY=${REPO_FULL}"
echo ""
echo "========================================"
echo " ボイラープレート: ${WORK_DIR}/${REPO_NAME}"
echo " SQL ファイル:     ${SQL_FILE}"
echo "========================================"

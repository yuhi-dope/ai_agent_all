-- Supabase 蓄積用スキーマ（MVP）
-- Supabase Dashboard の SQL Editor で実行するか、supabase db push で適用する。
-- 新規 DB でも既存 DB でも冪等に実行可能。

-- gen_random_uuid() を確実に使えるようにする
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. テーブル作成（IF NOT EXISTS: 既存なら何もしない）
-- ============================================================

CREATE TABLE IF NOT EXISTS companies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  employee_count TEXT,
  annual_revenue TEXT,
  industry TEXT,
  founded_date DATE,
  github_repository TEXT,
  github_token_secret_name TEXT,
  client_supabase_url TEXT,
  vercel_project_url TEXT,
  supabase_mgmt_token_enc TEXT,
  supabase_mgmt_token_expires_at TIMESTAMPTZ,
  vercel_token_enc TEXT,
  vercel_token_expires_at TIMESTAMPTZ,
  corporate_number TEXT,
  password_hash TEXT,
  onboarding JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_companies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  company_id UUID NOT NULL REFERENCES companies(id),
  role TEXT NOT NULL DEFAULT 'member',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, company_id)
);

CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  requirement_summary TEXT,
  spec_purpose TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  retry_count INT DEFAULT 0,
  output_subdir TEXT,
  genre TEXT,
  genre_override_reason TEXT,
  spec_markdown TEXT,
  notion_page_id TEXT,
  state_snapshot JSONB,
  company_id UUID REFERENCES companies(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS features (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  summary TEXT,
  file_list JSONB,
  company_id UUID REFERENCES companies(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rule_changes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  rule_name TEXT NOT NULL,
  added_block TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  genre TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  arguments JSONB,
  result_summary JSONB,
  source TEXT NOT NULL DEFAULT 'sandbox',
  logged_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  access_token TEXT NOT NULL,
  refresh_token TEXT,
  token_type TEXT DEFAULT 'Bearer',
  expires_at TIMESTAMPTZ,
  scopes TEXT,
  raw_response JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invite_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  token TEXT NOT NULL UNIQUE,
  created_by TEXT,
  role TEXT NOT NULL DEFAULT 'member',
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,
  consumed_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- 2. カラム追加 Migration（既存テーブルに不足カラムを追加）
--    新規作成時は全カラム揃っているので全部スキップされる
-- ============================================================

-- companies
ALTER TABLE companies ADD COLUMN IF NOT EXISTS employee_count TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS annual_revenue TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS industry TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS founded_date DATE;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS github_repository TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS github_token_secret_name TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS client_supabase_url TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS vercel_project_url TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS onboarding JSONB DEFAULT '{}';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS corporate_number TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS password_hash TEXT;

-- runs
ALTER TABLE runs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'started';
ALTER TABLE runs ADD COLUMN IF NOT EXISTS genre TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS genre_override_reason TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS spec_markdown TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS notion_page_id TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS state_snapshot JSONB;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);

-- features
ALTER TABLE features ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);

-- rule_changes
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS genre TEXT;

-- ============================================================
-- 3. インデックス作成（カラムが確実に存在した後に実行）
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

CREATE INDEX IF NOT EXISTS idx_user_companies_user ON user_companies(user_id);
CREATE INDEX IF NOT EXISTS idx_user_companies_company ON user_companies(company_id);

CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_genre ON runs(genre);
CREATE INDEX IF NOT EXISTS idx_runs_company_id ON runs(company_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE INDEX IF NOT EXISTS idx_features_run_id ON features(run_id);

CREATE INDEX IF NOT EXISTS idx_rule_changes_run_id ON rule_changes(run_id);
CREATE INDEX IF NOT EXISTS idx_rule_changes_status ON rule_changes(status);

CREATE INDEX IF NOT EXISTS idx_audit_logs_run_id ON audit_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_logged_at ON audit_logs(logged_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_tokens_provider_tenant
  ON oauth_tokens(provider, tenant_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider
  ON oauth_tokens(provider);

CREATE INDEX IF NOT EXISTS idx_invite_tokens_token ON invite_tokens(token);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_company ON invite_tokens(company_id);

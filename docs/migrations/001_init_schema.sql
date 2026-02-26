-- 001_init_schema.sql
-- 初期スキーマ: runs, features, rule_changes, audit_logs
-- docs/supabase_schema.sql から移行

-- runs: 各 run のメタデータ
CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  requirement_summary TEXT,
  spec_purpose TEXT,
  status TEXT NOT NULL,
  retry_count INT DEFAULT 0,
  output_subdir TEXT,
  genre TEXT,
  genre_override_reason TEXT,
  spec_markdown TEXT,
  notion_page_id TEXT,
  state_snapshot JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_genre ON runs(genre);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

-- features: run ごとの機能要約・生成ファイル一覧
CREATE TABLE IF NOT EXISTS features (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  summary TEXT,
  file_list JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_features_run_id ON features(run_id);

-- rule_changes: ルール自動マージ時の記録
CREATE TABLE IF NOT EXISTS rule_changes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  rule_name TEXT NOT NULL,
  added_block TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_changes_run_id ON rule_changes(run_id);

-- audit_logs: Sandbox MCP tool call trail
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

CREATE INDEX IF NOT EXISTS idx_audit_logs_run_id ON audit_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_logged_at ON audit_logs(logged_at DESC);

-- Supabase 蓄積用スキーマ（MVP）
-- Supabase Dashboard の SQL Editor で実行するか、supabase db push で適用する。

-- runs: 各 run のメタデータ
CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  requirement_summary TEXT,
  spec_purpose TEXT,
  status TEXT NOT NULL,
  retry_count INT DEFAULT 0,
  pr_url TEXT,
  output_subdir TEXT,
  genre TEXT,                     -- ジャンル ID（sfa / crm / accounting 等）
  genre_override_reason TEXT,     -- AI がユーザー指定ジャンルを上書きした場合の理由
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_genre ON runs(genre);

-- 既存テーブルへのマイグレーション（既存 DB に適用する場合）
-- ALTER TABLE runs ADD COLUMN IF NOT EXISTS genre TEXT;
-- ALTER TABLE runs ADD COLUMN IF NOT EXISTS genre_override_reason TEXT;

-- features: run ごとの機能要約・生成ファイル一覧
CREATE TABLE IF NOT EXISTS features (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  summary TEXT,
  file_list JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_features_run_id ON features(run_id);

-- rule_changes: ルール自動マージ時の記録（Phase 2 で利用）
CREATE TABLE IF NOT EXISTS rule_changes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id TEXT NOT NULL,
  rule_name TEXT NOT NULL,
  added_block TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_changes_run_id ON rule_changes(run_id);

-- 017_runs_failure_columns.sql
-- develop_agent の失敗パターン学習用カラムを runs テーブルに追加

ALTER TABLE runs ADD COLUMN IF NOT EXISTS failure_reason TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS failure_category TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS error_logs JSONB DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_runs_failure_category ON runs(failure_category)
  WHERE failure_category IS NOT NULL;

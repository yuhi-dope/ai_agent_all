-- Migration 024: オンボーディング3プラン対応
-- companies テーブルに onboarding_plan / onboarding_steps カラムを追加

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS onboarding_plan TEXT DEFAULT 'self'
    CHECK (onboarding_plan IN ('self', 'consul', 'full_support')),
  ADD COLUMN IF NOT EXISTS onboarding_steps JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN companies.onboarding_plan IS 'オンボーディングプラン: self / consul / full_support';
COMMENT ON COLUMN companies.onboarding_steps IS '手動完了ステップの記録 (JSON: {step_key: {completed: bool, completed_at, notes}})';

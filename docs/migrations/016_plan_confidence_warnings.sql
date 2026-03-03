-- 016: plan_confidence/plan_warnings カラムを saas_tasks テーブルに追加
-- タスク計画の確信度と注意事項を保存する

ALTER TABLE saas_tasks ADD COLUMN IF NOT EXISTS plan_confidence REAL DEFAULT 0.0;
ALTER TABLE saas_tasks ADD COLUMN IF NOT EXISTS plan_warnings JSONB DEFAULT '[]';

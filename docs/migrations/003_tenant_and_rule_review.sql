-- 003: テナント分離 (companies + user_companies) + ルールレビューワークフロー
-- 既存行は壊さない（company_id は nullable）

-- ============================================================
-- テナント: companies テーブル
-- ============================================================
CREATE TABLE IF NOT EXISTS companies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

-- ============================================================
-- テナント: user_companies マッピング
-- ============================================================
CREATE TABLE IF NOT EXISTS user_companies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  company_id UUID NOT NULL REFERENCES companies(id),
  role TEXT NOT NULL DEFAULT 'member',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(user_id, company_id)
);

CREATE INDEX IF NOT EXISTS idx_user_companies_user ON user_companies(user_id);
CREATE INDEX IF NOT EXISTS idx_user_companies_company ON user_companies(company_id);

-- ============================================================
-- 既存テーブルに company_id 追加
-- ============================================================
ALTER TABLE runs ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);
ALTER TABLE features ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);

CREATE INDEX IF NOT EXISTS idx_runs_company_id ON runs(company_id);
CREATE INDEX IF NOT EXISTS idx_features_company_id ON features(company_id);

-- ============================================================
-- rule_changes: レビューワークフロー用カラム追加
-- ============================================================
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE rule_changes ADD COLUMN IF NOT EXISTS genre TEXT;

CREATE INDEX IF NOT EXISTS idx_rule_changes_status ON rule_changes(status);

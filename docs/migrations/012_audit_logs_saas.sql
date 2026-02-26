-- 012: audit_logs テーブルに SaaS 操作用のカラムを追加
-- company_id, saas_name, genre, connection_id で SaaS 監査ログを記録できるようにする

ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS saas_name TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS genre TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS connection_id UUID;

-- SaaS 監査ログ検索用のインデックス
CREATE INDEX IF NOT EXISTS idx_audit_logs_company_id ON audit_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_saas_name ON audit_logs(saas_name);
CREATE INDEX IF NOT EXISTS idx_audit_logs_source ON audit_logs(source);

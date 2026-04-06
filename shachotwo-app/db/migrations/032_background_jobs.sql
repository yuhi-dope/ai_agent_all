-- 汎用バックグラウンドジョブ（kintone import 等）
-- 要件: shachotwo/b_詳細設計/b_10_kintone製造業リスト取得_要件定義.md §3-4

CREATE TABLE IF NOT EXISTS background_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    payload JSONB DEFAULT '{}',
    result JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_company_type_created
    ON background_jobs (company_id, job_type, created_at DESC);

ALTER TABLE background_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "background_jobs_company_isolation" ON background_jobs;
CREATE POLICY "background_jobs_company_isolation" ON background_jobs
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

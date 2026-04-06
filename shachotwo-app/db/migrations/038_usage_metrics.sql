-- 038_usage_metrics.sql
-- usage_metrics: BPOパイプライン実行・QA・コネクタ同期の使用量計測
-- REQ-1503: ARPUスケール設計 — 従量課金の基盤テーブル

CREATE TABLE IF NOT EXISTS usage_metrics (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    metric_type TEXT NOT NULL CHECK (metric_type IN ('pipeline_run', 'connector_sync', 'qa_query', 'seat')),
    quantity INT NOT NULL DEFAULT 1,
    unit_price_yen INT DEFAULT 500,
    pipeline_name TEXT,         -- pipeline_runの場合のパイプライン名
    period_month TEXT NOT NULL, -- 'YYYY-MM' 形式
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS
ALTER TABLE usage_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON usage_metrics
    USING (company_id = (SELECT company_id FROM users WHERE id = auth.uid()));

CREATE POLICY "service_insert" ON usage_metrics
    FOR INSERT WITH CHECK (true);  -- サービスロールからの記録を許可

-- Index
CREATE INDEX IF NOT EXISTS idx_usage_metrics_company_period ON usage_metrics(company_id, period_month);
CREATE INDEX IF NOT EXISTS idx_usage_metrics_type ON usage_metrics(company_id, metric_type, period_month);
CREATE INDEX IF NOT EXISTS idx_usage_metrics_created ON usage_metrics(created_at);

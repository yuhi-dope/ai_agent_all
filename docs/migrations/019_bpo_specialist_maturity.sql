-- BPO 専門化成熟度スコアテーブル
-- ジャンル×SaaS ごとの専門化度を定期的に集計・保存する

CREATE TABLE IF NOT EXISTS bpo_specialist_maturity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT,                   -- NULL = 全社横断集計
    saas_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    score NUMERIC(4,3) NOT NULL DEFAULT 0,  -- 0.000 ~ 1.000
    is_specialist BOOLEAN NOT NULL DEFAULT false,
    learned_rules_count INT NOT NULL DEFAULT 0,
    total_tasks INT NOT NULL DEFAULT 0,
    success_rate NUMERIC(4,3) NOT NULL DEFAULT 0,
    avg_confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, saas_name, genre)
);

CREATE INDEX IF NOT EXISTS idx_maturity_genre
    ON bpo_specialist_maturity(genre, saas_name);

ALTER TABLE bpo_specialist_maturity ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'bpo_specialist_maturity' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON bpo_specialist_maturity
            FOR ALL USING (
                company_id IS NULL
                OR company_id = current_setting('app.company_id', true)
            );
    END IF;
END $$;

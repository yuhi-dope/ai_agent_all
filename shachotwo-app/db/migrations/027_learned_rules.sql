-- 027_learned_rules.sql
-- 承認ワークフローで抽出された学習ルールを永続化するテーブル

CREATE TABLE IF NOT EXISTS learned_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    pipeline TEXT NOT NULL,
    step_name TEXT,
    rule_type TEXT NOT NULL DEFAULT 'correction',  -- correction / preference / pattern
    rule_text TEXT NOT NULL,
    source_execution_id UUID,
    confidence NUMERIC(3,2) DEFAULT 0.5,
    applied_count INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learned_rules_company ON learned_rules(company_id);
CREATE INDEX IF NOT EXISTS idx_learned_rules_pipeline ON learned_rules(company_id, pipeline);

ALTER TABLE learned_rules ENABLE ROW LEVEL SECURITY;

CREATE POLICY learned_rules_tenant ON learned_rules
    USING (company_id = current_setting('app.company_id')::uuid);

-- applied_count 一括インクリメント RPC
CREATE OR REPLACE FUNCTION increment_learned_rules_applied_count(rule_ids UUID[])
RETURNS VOID AS $$
BEGIN
    UPDATE learned_rules
    SET applied_count = applied_count + 1,
        updated_at    = now()
    WHERE id = ANY(rule_ids);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_learned_rules_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_learned_rules_updated_at
    BEFORE UPDATE ON learned_rules
    FOR EACH ROW EXECUTE FUNCTION update_learned_rules_updated_at();

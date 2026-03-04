-- SaaS 構造ナレッジ自動蓄積テーブル
-- BPO 実行中に読み取り系ツール（kintone_get_app_fields 等）の結果を自動保存する

CREATE TABLE IF NOT EXISTS saas_structure_knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id TEXT NOT NULL,
    saas_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,          -- アプリID、オブジェクト名等
    structure_type TEXT NOT NULL,      -- fields, layout, views, objects 等
    structure_data JSONB NOT NULL,     -- APIレスポンスをそのまま保存
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, saas_name, entity_id, structure_type)
);

CREATE INDEX IF NOT EXISTS idx_saas_knowledge_company
    ON saas_structure_knowledge(company_id, saas_name);

ALTER TABLE saas_structure_knowledge ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'saas_structure_knowledge' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON saas_structure_knowledge
            FOR ALL USING (company_id = current_setting('app.company_id', true));
    END IF;
END $$;

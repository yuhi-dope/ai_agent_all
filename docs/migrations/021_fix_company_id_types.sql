-- 021: company_id の型不整合を修正（TEXT → UUID）
-- 対象: channel_configs(010), saas_structure_knowledge(018), bpo_specialist_maturity(019)
-- 既存データの UUID 文字列を UUID 型にキャストする
-- ※ RLS ポリシーが company_id に依存するため、DROP → ALTER → 再作成の順で実行

-- ============================================================
-- saas_structure_knowledge: company_id TEXT → UUID
-- ============================================================
DROP POLICY IF EXISTS tenant_isolation ON saas_structure_knowledge;

ALTER TABLE saas_structure_knowledge
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE saas_structure_knowledge
    ADD CONSTRAINT fk_saas_knowledge_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

CREATE POLICY tenant_isolation ON saas_structure_knowledge
    FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);

-- ============================================================
-- bpo_specialist_maturity: company_id TEXT → UUID (NULLable)
-- ============================================================
DROP POLICY IF EXISTS tenant_isolation ON bpo_specialist_maturity;

ALTER TABLE bpo_specialist_maturity
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE bpo_specialist_maturity
    ADD CONSTRAINT fk_maturity_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

CREATE POLICY tenant_isolation ON bpo_specialist_maturity
    FOR ALL USING (
        company_id IS NULL
        OR company_id = current_setting('app.company_id', true)::UUID
    );

-- ============================================================
-- channel_configs: company_id TEXT → UUID
-- （RLS ポリシーなし → そのまま変更可能）
-- ============================================================
ALTER TABLE channel_configs
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE channel_configs
    ADD CONSTRAINT fk_channel_configs_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

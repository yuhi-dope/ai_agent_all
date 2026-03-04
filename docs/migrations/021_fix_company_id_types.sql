-- 021: company_id の型不整合を修正（TEXT → UUID）
-- 対象: channel_configs(010), saas_structure_knowledge(018), bpo_specialist_maturity(019)
-- 既存データの UUID 文字列を UUID 型にキャストする

-- ============================================================
-- channel_configs: company_id TEXT → UUID
-- ============================================================
ALTER TABLE channel_configs
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE channel_configs
    ADD CONSTRAINT fk_channel_configs_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

-- ============================================================
-- saas_structure_knowledge: company_id TEXT → UUID
-- ============================================================
ALTER TABLE saas_structure_knowledge
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE saas_structure_knowledge
    ADD CONSTRAINT fk_saas_knowledge_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

-- ============================================================
-- bpo_specialist_maturity: company_id TEXT → UUID (NULLable)
-- ============================================================
ALTER TABLE bpo_specialist_maturity
    ALTER COLUMN company_id TYPE UUID USING company_id::UUID;

ALTER TABLE bpo_specialist_maturity
    ADD CONSTRAINT fk_maturity_company
    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE;

-- 014_bpo_manufacturing.sql
-- 製造業BPO テーブル
-- Source of Truth: shachotwo/e_02_製造業BPO設計.md Section 1.3

-- =============================================================================
-- 1. 見積プロジェクト
-- =============================================================================
CREATE TABLE IF NOT EXISTS mfg_quotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    quote_number TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    project_name TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    material TEXT,
    surface_treatment TEXT,
    delivery_date DATE,
    total_amount BIGINT,
    profit_margin DECIMAL(5,2),
    status TEXT DEFAULT 'draft' CHECK (status IN ('draft','sent','won','lost','expired')),
    won_amount BIGINT,
    lost_reason TEXT,
    valid_until DATE,
    file_url TEXT,
    shape_type TEXT CHECK (shape_type IN ('round','block','plate','complex')),
    dimensions JSONB DEFAULT '{}',
    tolerances JSONB DEFAULT '{}',
    surface_roughness TEXT,
    features JSONB DEFAULT '[]',
    description TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE mfg_quotes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "mfg_quotes_company_isolation" ON mfg_quotes;
CREATE POLICY "mfg_quotes_company_isolation" ON mfg_quotes
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 2. 見積明細（工程別）
-- =============================================================================
CREATE TABLE IF NOT EXISTS mfg_quote_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id UUID NOT NULL REFERENCES mfg_quotes(id) ON DELETE CASCADE,
    company_id UUID NOT NULL REFERENCES companies(id),
    sort_order INTEGER NOT NULL,
    process_name TEXT NOT NULL,
    equipment TEXT,
    equipment_type TEXT,
    setup_time_min DECIMAL(8,1),
    cycle_time_min DECIMAL(8,1),
    total_time_min DECIMAL(10,1),
    charge_rate DECIMAL(10,0),
    process_cost BIGINT,
    material_cost BIGINT,
    outsource_cost BIGINT,
    cost_source TEXT DEFAULT 'ai_estimated' CHECK (cost_source IN ('manual','past_record','ai_estimated')),
    confidence DECIMAL(3,2),
    ai_estimated_time DECIMAL(10,1),
    user_modified BOOLEAN DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE mfg_quote_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "mfg_quote_items_company_isolation" ON mfg_quote_items;
CREATE POLICY "mfg_quote_items_company_isolation" ON mfg_quote_items
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 3. チャージレートマスタ
-- =============================================================================
CREATE TABLE IF NOT EXISTS mfg_charge_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    equipment_name TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    charge_rate DECIMAL(10,0) NOT NULL,
    setup_time_default DECIMAL(8,1),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(company_id, equipment_name)
);

ALTER TABLE mfg_charge_rates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "mfg_charge_rates_company_isolation" ON mfg_charge_rates;
CREATE POLICY "mfg_charge_rates_company_isolation" ON mfg_charge_rates
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 4. 材料単価マスタ
-- =============================================================================
CREATE TABLE IF NOT EXISTS mfg_material_prices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    material_code TEXT NOT NULL,
    material_name TEXT NOT NULL,
    form TEXT NOT NULL,
    size_spec TEXT,
    unit TEXT NOT NULL,
    unit_price DECIMAL(10,0) NOT NULL,
    supplier TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(company_id, material_code, form, size_spec)
);

ALTER TABLE mfg_material_prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "mfg_material_prices_company_isolation" ON mfg_material_prices;
CREATE POLICY "mfg_material_prices_company_isolation" ON mfg_material_prices
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- インデックス
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_mfg_quotes_company ON mfg_quotes(company_id);
CREATE INDEX IF NOT EXISTS idx_mfg_quotes_status ON mfg_quotes(company_id, status);
CREATE INDEX IF NOT EXISTS idx_mfg_quotes_customer ON mfg_quotes(company_id, customer_name);
CREATE INDEX IF NOT EXISTS idx_mfg_quote_items_quote ON mfg_quote_items(quote_id);
CREATE INDEX IF NOT EXISTS idx_mfg_quote_items_company ON mfg_quote_items(company_id);
CREATE INDEX IF NOT EXISTS idx_mfg_charge_rates_company ON mfg_charge_rates(company_id);
CREATE INDEX IF NOT EXISTS idx_mfg_material_prices_company ON mfg_material_prices(company_id);
CREATE INDEX IF NOT EXISTS idx_mfg_material_prices_code ON mfg_material_prices(company_id, material_code);

-- 007_bpo_construction.sql
-- 建設業BPOテーブル
-- Source of Truth: shachotwo/e_01_建設業BPO設計.md

-- =============================================================================
-- 1. estimation_projects — 積算プロジェクト
-- =============================================================================
CREATE TABLE estimation_projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL,                      -- 工事名
    project_type TEXT NOT NULL               -- public_civil / public_building / private_civil / private_building
        CHECK (project_type IN ('public_civil', 'public_building', 'private_civil', 'private_building')),
    region TEXT NOT NULL,                    -- 都道府県
    municipality TEXT,                       -- 市区町村
    fiscal_year INTEGER NOT NULL,            -- 年度（単価基準年度）
    client_name TEXT,                        -- 発注者名
    design_amount BIGINT,                    -- 設計金額（円）※公共工事の場合
    estimated_amount BIGINT,                 -- 積算金額（円）
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_progress', 'review', 'submitted', 'won', 'lost')),
    overhead_rates JSONB DEFAULT '{}',       -- 諸経費率
    metadata JSONB DEFAULT '{}',             -- 工期、施工条件等
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE estimation_projects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "estimation_projects_tenant" ON estimation_projects;
CREATE POLICY "estimation_projects_tenant" ON estimation_projects
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_estimation_projects_company ON estimation_projects(company_id);
CREATE INDEX IF NOT EXISTS idx_estimation_projects_status ON estimation_projects(company_id, status);

-- =============================================================================
-- 2. estimation_items — 積算明細
-- =============================================================================
CREATE TABLE estimation_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES estimation_projects(id) ON DELETE CASCADE,
    company_id UUID NOT NULL REFERENCES companies(id),
    sort_order INTEGER NOT NULL,
    category TEXT NOT NULL,                  -- 工種
    subcategory TEXT,                        -- 種別
    detail TEXT,                             -- 細別
    specification TEXT,                      -- 規格
    quantity NUMERIC(15,3) NOT NULL,
    unit TEXT NOT NULL,                      -- m3, m2, m, 本, 式 等
    unit_price NUMERIC(15,2),
    amount BIGINT,                           -- quantity × unit_price
    price_source TEXT                        -- manual / past_record / labor_rate / market_price / ai_estimated
        CHECK (price_source IS NULL OR price_source IN ('manual', 'past_record', 'labor_rate', 'market_price', 'ai_estimated')),
    price_confidence NUMERIC(3,2),           -- 0.00-1.00
    source_document TEXT,                    -- 拾い出し元
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE estimation_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "estimation_items_tenant" ON estimation_items;
CREATE POLICY "estimation_items_tenant" ON estimation_items
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_estimation_items_project ON estimation_items(project_id);

-- =============================================================================
-- 3. unit_price_master — 単価マスタ（自社実績ベース）
-- =============================================================================
CREATE TABLE unit_price_master (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    category TEXT NOT NULL,
    subcategory TEXT,
    detail TEXT,
    specification TEXT,
    unit TEXT NOT NULL,
    unit_price NUMERIC(15,2) NOT NULL,
    price_type TEXT NOT NULL                 -- labor / material / machine / composite
        CHECK (price_type IN ('labor', 'material', 'machine', 'composite')),
    region TEXT,
    year INTEGER,
    source TEXT NOT NULL                     -- manual_input / past_estimation / public_labor_rate / market_survey
        CHECK (source IN ('manual_input', 'past_estimation', 'public_labor_rate', 'market_survey')),
    source_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE unit_price_master ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "unit_price_master_tenant" ON unit_price_master;
CREATE POLICY "unit_price_master_tenant" ON unit_price_master
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_unit_price_master_company ON unit_price_master(company_id);
CREATE INDEX IF NOT EXISTS idx_unit_price_master_lookup ON unit_price_master(company_id, category, subcategory, region);

-- =============================================================================
-- 4. public_labor_rates — 公共工事設計労務単価（全社共通・読取専用）
-- =============================================================================
CREATE TABLE public_labor_rates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fiscal_year INTEGER NOT NULL,
    region TEXT NOT NULL,                    -- 都道府県
    occupation TEXT NOT NULL,                -- 職種
    daily_rate INTEGER NOT NULL,             -- 日額（円）
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(fiscal_year, region, occupation)
);

-- 全社共通データのため、RLSは読み取り許可のみ
ALTER TABLE public_labor_rates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "public_labor_rates_read_all" ON public_labor_rates;
CREATE POLICY "public_labor_rates_read_all" ON public_labor_rates
    FOR SELECT USING (true);

CREATE INDEX IF NOT EXISTS idx_public_labor_rates_lookup ON public_labor_rates(fiscal_year, region);

-- =============================================================================
-- 5. estimation_templates — 積算テンプレート（全社共通）
-- =============================================================================
CREATE TABLE estimation_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    project_type TEXT NOT NULL,
    category TEXT NOT NULL,                  -- civil / building
    items JSONB NOT NULL,                    -- 標準工種構成
    overhead_defaults JSONB NOT NULL,        -- デフォルト諸経費率
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE estimation_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "estimation_templates_read_all" ON estimation_templates;
CREATE POLICY "estimation_templates_read_all" ON estimation_templates
    FOR SELECT USING (true);

-- =============================================================================
-- 6. construction_sites — 現場マスタ
-- =============================================================================
CREATE TABLE construction_sites (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL,
    address TEXT,
    client_name TEXT,
    contract_amount BIGINT,
    start_date DATE,
    end_date DATE,
    site_manager_name TEXT,                  -- 現場代理人名
    safety_officer_name TEXT,                -- 安全衛生責任者名
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('planning', 'active', 'completed')),
    green_file_format TEXT DEFAULT 'zenken'
        CHECK (green_file_format IN ('zenken', 'custom', 'greensite')),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE construction_sites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "construction_sites_tenant" ON construction_sites;
CREATE POLICY "construction_sites_tenant" ON construction_sites
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_construction_sites_company ON construction_sites(company_id);
CREATE INDEX IF NOT EXISTS idx_construction_sites_status ON construction_sites(company_id, status);

-- =============================================================================
-- 7. workers — 作業員マスタ
-- =============================================================================
CREATE TABLE construction_workers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name_kana TEXT,
    first_name_kana TEXT,
    birth_date DATE,
    blood_type TEXT CHECK (blood_type IS NULL OR blood_type IN ('A', 'B', 'O', 'AB')),
    address TEXT,
    phone TEXT,
    hire_date DATE,
    experience_years INTEGER,
    health_check_date DATE,
    health_check_result TEXT,
    social_insurance JSONB DEFAULT '{}',
    emergency_contact JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE construction_workers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "construction_workers_tenant" ON construction_workers;
CREATE POLICY "construction_workers_tenant" ON construction_workers
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_construction_workers_company ON construction_workers(company_id);

-- =============================================================================
-- 8. worker_qualifications — 資格マスタ
-- =============================================================================
CREATE TABLE worker_qualifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    worker_id UUID NOT NULL REFERENCES construction_workers(id) ON DELETE CASCADE,
    company_id UUID NOT NULL REFERENCES companies(id),
    qualification_type TEXT NOT NULL         -- license / special_training / skill_training
        CHECK (qualification_type IN ('license', 'special_training', 'skill_training')),
    qualification_name TEXT NOT NULL,
    certificate_number TEXT,
    issued_date DATE,
    expiry_date DATE,
    issuer TEXT,
    certificate_image_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE worker_qualifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "worker_qualifications_tenant" ON worker_qualifications;
CREATE POLICY "worker_qualifications_tenant" ON worker_qualifications
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_worker_qualifications_worker ON worker_qualifications(worker_id);
CREATE INDEX IF NOT EXISTS idx_worker_qualifications_expiry ON worker_qualifications(company_id, expiry_date);

-- =============================================================================
-- 9. site_worker_assignments — 現場×作業員アサイン
-- =============================================================================
CREATE TABLE site_worker_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES construction_sites(id) ON DELETE CASCADE,
    worker_id UUID NOT NULL REFERENCES construction_workers(id),
    company_id UUID NOT NULL REFERENCES companies(id),
    entry_date DATE NOT NULL,
    exit_date DATE,
    role TEXT,                               -- 職長、作業員等
    entry_education_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(site_id, worker_id, entry_date)
);

ALTER TABLE site_worker_assignments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "site_worker_assignments_tenant" ON site_worker_assignments;
CREATE POLICY "site_worker_assignments_tenant" ON site_worker_assignments
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_site_worker_assignments_site ON site_worker_assignments(site_id);
CREATE INDEX IF NOT EXISTS idx_site_worker_assignments_worker ON site_worker_assignments(worker_id);

-- =============================================================================
-- 10. safety_documents — 生成された安全書類
-- =============================================================================
CREATE TABLE safety_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES construction_sites(id),
    company_id UUID NOT NULL REFERENCES companies(id),
    document_type TEXT NOT NULL,             -- worker_roster / qualification_list / subcontractor_chart / safety_plan
    document_number TEXT,                    -- 全建統一様式番号
    version INTEGER DEFAULT 1,
    generated_data JSONB NOT NULL,
    file_url TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'submitted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE safety_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "safety_documents_tenant" ON safety_documents;
CREATE POLICY "safety_documents_tenant" ON safety_documents
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_safety_documents_site ON safety_documents(site_id);

-- =============================================================================
-- 11. subcontractors — 下請業者マスタ（bpo_vendorsを建設業用に拡張）
-- =============================================================================
CREATE TABLE subcontractors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    vendor_id UUID REFERENCES bpo_vendors(id),  -- ベースベンダーとの紐づけ
    name TEXT NOT NULL,
    representative TEXT,
    address TEXT,
    phone TEXT,
    license_number TEXT,                     -- 建設業許可番号
    license_expiry DATE,
    specialties TEXT[],                      -- 得意工種
    insurance JSONB DEFAULT '{}',
    evaluation JSONB DEFAULT '{}',           -- {quality, schedule, safety, price, overall}
    evaluation_date DATE,
    bank_info JSONB DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE subcontractors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "subcontractors_tenant" ON subcontractors;
CREATE POLICY "subcontractors_tenant" ON subcontractors
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_subcontractors_company ON subcontractors(company_id);

-- =============================================================================
-- 12. construction_contracts — 工事台帳
-- =============================================================================
CREATE TABLE construction_contracts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    site_id UUID REFERENCES construction_sites(id),
    contract_number TEXT,
    client_name TEXT NOT NULL,
    project_name TEXT NOT NULL,
    contract_amount BIGINT NOT NULL,
    tax_rate NUMERIC(4,3) DEFAULT 0.10,
    contract_date DATE,
    start_date DATE,
    completion_date DATE,
    payment_terms TEXT,
    billing_type TEXT DEFAULT 'monthly'
        CHECK (billing_type IN ('monthly', 'milestone', 'completion')),
    items JSONB NOT NULL,                    -- 内訳
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE construction_contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "construction_contracts_tenant" ON construction_contracts;
CREATE POLICY "construction_contracts_tenant" ON construction_contracts
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_construction_contracts_company ON construction_contracts(company_id);

-- =============================================================================
-- 13. progress_records — 月次出来高
-- =============================================================================
CREATE TABLE progress_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contract_id UUID NOT NULL REFERENCES construction_contracts(id),
    company_id UUID NOT NULL REFERENCES companies(id),
    period_year INTEGER NOT NULL,
    period_month INTEGER NOT NULL,
    items JSONB NOT NULL,                    -- [{item_name, contract_amount, progress_rate, progress_amount}]
    cumulative_amount BIGINT NOT NULL,
    previous_cumulative BIGINT NOT NULL,
    current_amount BIGINT,                   -- cumulative - previous
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'confirmed', 'billed')),
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(contract_id, period_year, period_month)
);

ALTER TABLE progress_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "progress_records_tenant" ON progress_records;
CREATE POLICY "progress_records_tenant" ON progress_records
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_progress_records_contract ON progress_records(contract_id);

-- =============================================================================
-- 14. cost_records — 工事原価（実績）
-- =============================================================================
CREATE TABLE cost_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    contract_id UUID NOT NULL REFERENCES construction_contracts(id),
    record_date DATE NOT NULL,
    cost_type TEXT NOT NULL                  -- material / labor / subcontract / equipment / overhead
        CHECK (cost_type IN ('material', 'labor', 'subcontract', 'equipment', 'overhead')),
    description TEXT NOT NULL,
    amount BIGINT NOT NULL,
    vendor_name TEXT,
    invoice_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cost_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "cost_records_tenant" ON cost_records;
CREATE POLICY "cost_records_tenant" ON cost_records
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_cost_records_contract ON cost_records(contract_id);
CREATE INDEX IF NOT EXISTS idx_cost_records_company_date ON cost_records(company_id, record_date);

-- 006_bpo_base.sql
-- ベースBPO共通テーブル（全業種で使用）
-- Source of Truth: shachotwo/d_00_BPOアーキテクチャ設計.md §8

-- =============================================================================
-- 1. bpo_invoices — 請求書（汎用）
-- =============================================================================
CREATE TABLE bpo_invoices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    invoice_number TEXT NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    client_name TEXT NOT NULL,
    subtotal BIGINT NOT NULL,
    tax_rate NUMERIC(4,3) DEFAULT 0.10,
    tax_amount BIGINT NOT NULL,
    total BIGINT NOT NULL,
    items JSONB NOT NULL,                    -- [{description, quantity, unit_price, amount}]
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'sent', 'paid', 'overdue', 'cancelled')),
    source_type TEXT,                        -- manual / progress_billing / delivery / treatment
    source_id UUID,                          -- 元データへの参照（出来高ID、納品ID等）
    file_url TEXT,
    sent_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE bpo_invoices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "bpo_invoices_tenant_isolation" ON bpo_invoices;
CREATE POLICY "bpo_invoices_tenant_isolation" ON bpo_invoices
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_bpo_invoices_company ON bpo_invoices(company_id);
CREATE INDEX IF NOT EXISTS idx_bpo_invoices_status ON bpo_invoices(company_id, status);
CREATE INDEX IF NOT EXISTS idx_bpo_invoices_due_date ON bpo_invoices(company_id, due_date);

-- =============================================================================
-- 2. bpo_expenses — 経費記録
-- =============================================================================
CREATE TABLE bpo_expenses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    user_id UUID NOT NULL REFERENCES users(id),
    expense_date DATE NOT NULL,
    category TEXT NOT NULL,                  -- transportation / supplies / entertainment / etc.
    description TEXT NOT NULL,
    amount BIGINT NOT NULL,
    tax_included BOOLEAN DEFAULT true,
    receipt_url TEXT,                         -- 領収書画像URL
    ocr_data JSONB,                          -- OCR読み取り結果
    account_code TEXT,                       -- 勘定科目（AI推定 or 手動）
    cost_center TEXT,                        -- 原価センター（現場ID、部門等）
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE bpo_expenses ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "bpo_expenses_tenant_isolation" ON bpo_expenses;
CREATE POLICY "bpo_expenses_tenant_isolation" ON bpo_expenses
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_bpo_expenses_company ON bpo_expenses(company_id);
CREATE INDEX IF NOT EXISTS idx_bpo_expenses_user ON bpo_expenses(company_id, user_id);

-- =============================================================================
-- 3. bpo_vendors — ベンダー（仕入先・外注先の共通マスタ）
-- =============================================================================
CREATE TABLE bpo_vendors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL,
    vendor_type TEXT NOT NULL,               -- subcontractor / supplier / service_provider / lab
    representative TEXT,
    address TEXT,
    phone TEXT,
    email TEXT,
    payment_terms TEXT,
    bank_info JSONB DEFAULT '{}',
    license_info JSONB DEFAULT '{}',         -- 許認可情報（業種により異なる）
    evaluation JSONB DEFAULT '{}',           -- 評価スコア
    evaluation_date DATE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    industry_data JSONB DEFAULT '{}',        -- 業種固有の追加データ
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE bpo_vendors ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "bpo_vendors_tenant_isolation" ON bpo_vendors;
CREATE POLICY "bpo_vendors_tenant_isolation" ON bpo_vendors
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_bpo_vendors_company ON bpo_vendors(company_id);
CREATE INDEX IF NOT EXISTS idx_bpo_vendors_type ON bpo_vendors(company_id, vendor_type);

-- =============================================================================
-- 4. bpo_permits — 許認可・届出管理
-- =============================================================================
CREATE TABLE bpo_permits (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    permit_type TEXT NOT NULL,               -- construction_license / iso_cert / health_center / etc.
    permit_name TEXT NOT NULL,
    permit_number TEXT,
    issued_date DATE,
    expiry_date DATE,
    renewal_lead_days INTEGER DEFAULT 180,   -- 更新準備開始日（期限のN日前）
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'expiring', 'expired', 'renewed')),
    required_documents JSONB DEFAULT '[]',   -- 必要書類リスト
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE bpo_permits ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "bpo_permits_tenant_isolation" ON bpo_permits;
CREATE POLICY "bpo_permits_tenant_isolation" ON bpo_permits
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_bpo_permits_company ON bpo_permits(company_id);
CREATE INDEX IF NOT EXISTS idx_bpo_permits_expiry ON bpo_permits(company_id, expiry_date);

-- =============================================================================
-- 5. bpo_approvals — 承認ワークフロー（汎用）
-- =============================================================================
CREATE TABLE bpo_approvals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    target_type TEXT NOT NULL,               -- invoice / expense / estimation / progress / etc.
    target_id UUID NOT NULL,
    requested_by UUID NOT NULL REFERENCES users(id),
    approver_id UUID REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    comment TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ
);

ALTER TABLE bpo_approvals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "bpo_approvals_tenant_isolation" ON bpo_approvals;
CREATE POLICY "bpo_approvals_tenant_isolation" ON bpo_approvals
    USING (company_id = current_setting('app.company_id', true)::uuid);

CREATE INDEX IF NOT EXISTS idx_bpo_approvals_company ON bpo_approvals(company_id);
CREATE INDEX IF NOT EXISTS idx_bpo_approvals_target ON bpo_approvals(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_bpo_approvals_pending ON bpo_approvals(company_id, status) WHERE status = 'pending';

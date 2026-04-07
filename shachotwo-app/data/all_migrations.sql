-- === 001_initial_schema.sql ===

-- シャチョツー（社長2号）— MVP Database Schema
-- 12 tables with RLS, indexes, triggers
-- Source of Truth: shachotwo/c_02_プロダクト設計.md §5

-- =============================================================================
-- Extensions
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- =============================================================================
-- 1. companies — テナント管理
-- =============================================================================
CREATE TABLE companies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE,
    industry TEXT NOT NULL,
    sub_industry TEXT,
    employee_count INT,
    org_structure JSONB,
    genome_template_id UUID,          -- Phase 2+: FK to genome_templates
    genome_customizations JSONB,
    onboarding_progress NUMERIC(5,2) DEFAULT 0.00,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 2. users — ユーザー管理
-- MVP: 1ユーザー = 1会社。Phase 2+で user_companies 中間テーブル追加
-- =============================================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'editor')),
    department TEXT,
    responsibilities TEXT[],
    can_input_knowledge BOOLEAN DEFAULT true,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 3. knowledge_sessions — ナレッジ入力セッション
-- =============================================================================
CREATE TABLE knowledge_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    user_id UUID NOT NULL REFERENCES users(id),
    input_type TEXT NOT NULL CHECK (input_type IN ('voice', 'text', 'document', 'interactive', 'inferred')),
    raw_content TEXT,
    audio_url TEXT,
    document_url TEXT,
    inference_source TEXT,
    extraction_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed', 'failed_permanent')),
    extraction_error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 4. knowledge_items — 構造化ナレッジ（コア）
-- 楽観的ロック: version カラム。409 VERSION_CONFLICT で競合検出
-- =============================================================================
CREATE TABLE knowledge_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    session_id UUID REFERENCES knowledge_sessions(id),
    department TEXT NOT NULL,
    category TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('rule', 'flow', 'decision_logic', 'fact', 'tip')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    conditions JSONB,
    examples JSONB,
    exceptions JSONB,
    source_type TEXT NOT NULL CHECK (source_type IN ('explicit', 'inferred', 'template')),
    source_user_id UUID REFERENCES users(id),
    confidence NUMERIC(4,3) CHECK (confidence >= 0 AND confidence <= 1),
    version INT NOT NULL DEFAULT 1,
    superseded_by UUID REFERENCES knowledge_items(id),
    is_active BOOLEAN DEFAULT true,
    embedding VECTOR(512),            -- Voyage AI voyage-3, 512 dimensions
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 5. knowledge_relations — ナレッジ間関係
-- =============================================================================
CREATE TABLE knowledge_relations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    source_id UUID NOT NULL REFERENCES knowledge_items(id),
    target_id UUID NOT NULL REFERENCES knowledge_items(id),
    relation_type TEXT NOT NULL
        CHECK (relation_type IN ('depends_on', 'contradicts', 'refines', 'part_of', 'triggers', 'overrides', 'prerequisite_for', 'example_of')),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 6. company_state_snapshots — デジタルツイン（5次元 MVP, 9次元 Phase 2+）
-- =============================================================================
CREATE TABLE company_state_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    people_state JSONB,               -- ① ヒト【MVP】
    process_state JSONB,              -- ② プロセス【MVP】
    cost_state JSONB,                 -- ③ コスト【MVP】
    tool_state JSONB,                 -- ④ ツール【MVP】
    risk_state JSONB,                 -- ⑤ リスク【MVP】
    customer_state JSONB,             -- ⑥ 顧客【Phase 2+】
    information_state JSONB,          -- ⑦ 情報【Phase 2+】
    culture_state JSONB,              -- ⑧ 文化【Phase 2+】
    growth_state JSONB,               -- ⑨ 成長【Phase 2+】
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 7. proactive_proposals — 能動提案
-- =============================================================================
CREATE TABLE proactive_proposals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    proposal_type TEXT NOT NULL
        CHECK (proposal_type IN ('risk_alert', 'improvement', 'rule_challenge', 'opportunity')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    impact_estimate JSONB,
    evidence JSONB,
    related_knowledge_ids UUID[],
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'reviewed', 'accepted', 'rejected', 'implemented')),
    reviewed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 8. decision_rules — 意思決定ルール
-- =============================================================================
CREATE TABLE decision_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    department TEXT NOT NULL,
    decision_name TEXT NOT NULL,
    context TEXT NOT NULL,
    logic_type TEXT NOT NULL CHECK (logic_type IN ('formula', 'if_then', 'matrix', 'heuristic')),
    logic_definition JSONB NOT NULL,
    variables JSONB,
    data_validation JSONB,
    improvement_proposed BOOLEAN DEFAULT false,
    source_user_id UUID REFERENCES users(id),
    examples JSONB,
    exceptions JSONB,
    is_active BOOLEAN DEFAULT true,
    version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 9. tool_connections — SaaS接続管理
-- =============================================================================
CREATE TABLE tool_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    tool_name TEXT NOT NULL,
    tool_type TEXT NOT NULL CHECK (tool_type IN ('saas', 'cli', 'api', 'manual')),
    connection_method TEXT NOT NULL CHECK (connection_method IN ('api', 'ipaas', 'rpa')),
    connection_config JSONB,          -- encrypted at app level
    method_auto_selected BOOLEAN,
    fallback_method TEXT,
    mapped_departments TEXT[],
    mapped_processes TEXT[],
    health_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (health_status IN ('healthy', 'degraded', 'down', 'unknown')),
    last_health_check TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 10. execution_logs — BPO実行ログ（削除不可・3年保持）
-- =============================================================================
CREATE TABLE execution_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    flow_id UUID,                     -- Phase 2+: FK to department_flows
    triggered_by TEXT CHECK (triggered_by IN ('user', 'schedule', 'event', 'proactive')),
    operations JSONB NOT NULL,
    overall_success BOOLEAN,
    time_saved_minutes INT,
    cost_saved_yen INT,
    lessons_learned JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 11. audit_logs — 監査ログ（削除不可・5年保持）
-- =============================================================================
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL,         -- no FK for insert performance
    user_id UUID,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id UUID,
    details JSONB,                    -- includes old_values for updates
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- 12. consent_records — 同意管理（削除不可・法的要件）
-- =============================================================================
CREATE TABLE consent_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    user_id UUID NOT NULL REFERENCES users(id),
    consent_type TEXT NOT NULL CHECK (consent_type IN (
        'knowledge_collection', 'data_processing', 'behavior_inference',
        'benchmark_sharing', 'right_to_deletion', 'data_portability', 'partner_data_sharing'
    )),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    consent_version TEXT NOT NULL DEFAULT '1.0',
    ip_address INET,
    user_agent TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Indexes
-- =============================================================================

-- Vector search (HNSW cosine)
CREATE INDEX idx_knowledge_items_embedding ON knowledge_items
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree: company_id (tenant isolation performance)
CREATE INDEX idx_users_company ON users (company_id);
CREATE INDEX idx_knowledge_sessions_company_status ON knowledge_sessions (company_id, extraction_status);
CREATE INDEX idx_knowledge_items_company_active ON knowledge_items (company_id, is_active, department);
CREATE INDEX idx_knowledge_items_company_category ON knowledge_items (company_id, category);
CREATE INDEX idx_knowledge_relations_company ON knowledge_relations (company_id);
CREATE INDEX idx_knowledge_relations_source ON knowledge_relations (source_id);
CREATE INDEX idx_knowledge_relations_target ON knowledge_relations (target_id);
CREATE INDEX idx_snapshots_company ON company_state_snapshots (company_id, snapshot_at DESC);
CREATE INDEX idx_proposals_company_status ON proactive_proposals (company_id, status);
CREATE INDEX idx_decision_rules_company ON decision_rules (company_id, department, is_active);
CREATE INDEX idx_tool_connections_company ON tool_connections (company_id, health_status);
CREATE INDEX idx_execution_logs_company ON execution_logs (company_id, created_at DESC);
CREATE INDEX idx_audit_logs_company_time ON audit_logs (company_id, created_at DESC);
CREATE INDEX idx_audit_logs_target ON audit_logs (target_type, target_id);
CREATE INDEX idx_consent_records_company_user ON consent_records (company_id, user_id);
CREATE INDEX idx_consent_records_active ON consent_records (company_id) WHERE revoked_at IS NULL;

-- =============================================================================
-- Row Level Security (RLS) — company_id tenant isolation on ALL tables
-- =============================================================================

ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
CREATE POLICY "companies_tenant_isolation" ON companies
    USING (id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_tenant_isolation" ON users
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE knowledge_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "knowledge_sessions_tenant_isolation" ON knowledge_sessions
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE knowledge_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY "knowledge_items_tenant_isolation" ON knowledge_items
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE knowledge_relations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "knowledge_relations_tenant_isolation" ON knowledge_relations
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE company_state_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "snapshots_tenant_isolation" ON company_state_snapshots
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE proactive_proposals ENABLE ROW LEVEL SECURITY;
CREATE POLICY "proposals_tenant_isolation" ON proactive_proposals
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE decision_rules ENABLE ROW LEVEL SECURITY;
CREATE POLICY "decision_rules_tenant_isolation" ON decision_rules
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE tool_connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tool_connections_tenant_isolation" ON tool_connections
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE execution_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "execution_logs_tenant_isolation" ON execution_logs
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "audit_logs_tenant_isolation" ON audit_logs
    USING (company_id = (current_setting('app.company_id', true))::UUID);

ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY "consent_records_tenant_isolation" ON consent_records
    USING (company_id = (current_setting('app.company_id', true))::UUID);

-- =============================================================================
-- Triggers: auto-update updated_at
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_knowledge_items_updated_at
    BEFORE UPDATE ON knowledge_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_decision_rules_updated_at
    BEFORE UPDATE ON decision_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- === 002_vector_search_function.sql ===

-- Vector search RPC function for pgvector cosine similarity
-- Used by brain/knowledge/search.py

CREATE OR REPLACE FUNCTION match_knowledge_items(
    query_embedding VECTOR(512),
    match_company_id UUID,
    match_department TEXT DEFAULT NULL,
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    content TEXT,
    department TEXT,
    category TEXT,
    item_type TEXT,
    confidence NUMERIC,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ki.id, ki.title, ki.content, ki.department, ki.category,
        ki.item_type, ki.confidence,
        (1 - (ki.embedding <=> query_embedding))::FLOAT AS similarity
    FROM knowledge_items ki
    WHERE ki.company_id = match_company_id
        AND ki.is_active = true
        AND ki.embedding IS NOT NULL
        AND (match_department IS NULL OR ki.department = match_department)
        AND (1 - (ki.embedding <=> query_embedding)) >= match_threshold
    ORDER BY ki.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- === 003_embedding_768.sql ===

-- Switch embedding from 512 (Voyage) to 768 (Gemini text-embedding-004)

-- Clear existing embeddings (generated with wrong dimensions)
UPDATE knowledge_items SET embedding = NULL;

-- Change column type
ALTER TABLE knowledge_items ALTER COLUMN embedding TYPE VECTOR(768);

-- Recreate HNSW index for 768 dimensions
DROP INDEX IF EXISTS idx_knowledge_items_embedding;
CREATE INDEX idx_knowledge_items_embedding ON knowledge_items
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Recreate RPC function for 768 dimensions
CREATE OR REPLACE FUNCTION match_knowledge_items(
    query_embedding VECTOR(768),
    match_company_id UUID,
    match_department TEXT DEFAULT NULL,
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    content TEXT,
    department TEXT,
    category TEXT,
    item_type TEXT,
    confidence NUMERIC,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ki.id, ki.title, ki.content, ki.department, ki.category,
        ki.item_type, ki.confidence,
        (1 - (ki.embedding <=> query_embedding))::FLOAT AS similarity
    FROM knowledge_items ki
    WHERE ki.company_id = match_company_id
        AND ki.is_active = true
        AND ki.embedding IS NOT NULL
        AND (match_department IS NULL OR ki.department = match_department)
        AND (1 - (ki.embedding <=> query_embedding)) >= match_threshold
    ORDER BY ki.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- === 004_audit_logs_insert_only_policy.sql ===

-- Migration 002: audit_logs INSERT-only policy
-- Per security design (a_01_セキュリティ設計.md §13):
--   audit_logs table is INSERT-only (no UPDATE/DELETE allowed).
--   Existing tenant_isolation policy (from 001) allows SELECT.
--   This adds an explicit INSERT policy and removes UPDATE/DELETE ability.

-- Add INSERT-only policy (anyone can insert audit logs for their company)
CREATE POLICY "audit_logs_insert_only" ON audit_logs
    FOR INSERT
    WITH CHECK (true);

-- Add explicit SELECT policy scoped to company
-- (tenant_isolation already covers this, but being explicit for clarity)
-- No UPDATE or DELETE policies = those operations are blocked by RLS.


-- === 005_invitations.sql ===

-- 005_invitations.sql — メンバー招待テーブル
-- 管理者が同じ会社のメンバーを招待するための仕組み

CREATE TABLE invitations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('admin', 'editor')),
    invited_by UUID NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'expired', 'cancelled')),
    accepted_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 同一会社・同一メールの重複招待を防止（pending状態のもの）
CREATE UNIQUE INDEX idx_invitations_unique_pending
    ON invitations (company_id, email)
    WHERE status = 'pending';

-- テナント分離インデックス
CREATE INDEX idx_invitations_company ON invitations (company_id, status);
CREATE INDEX idx_invitations_email ON invitations (email, status);

-- RLS
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "invitations_tenant_isolation" ON invitations
    USING (company_id = (current_setting('app.company_id', true))::UUID);


-- === 006_bpo_base.sql ===

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


-- === 007_bpo_construction.sql ===

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


-- === 008_qa_sessions.sql ===

-- Q&A履歴テーブル: 社長の質問→回答を記録し、ナレッジの穴発見・BPO優先度・信頼度調整に活用
CREATE TABLE qa_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT,
    -- 回答に使ったナレッジのID群
    referenced_knowledge_ids UUID[] DEFAULT '{}',
    -- 回答できたか（answered / no_match / partial）
    answer_status TEXT NOT NULL DEFAULT 'answered',
    -- ユーザー評価（任意）
    user_rating TEXT,          -- helpful / wrong / outdated
    user_feedback TEXT,        -- 自由記述「これ違う、今は60時間」
    -- LLMメタ
    model_used TEXT,
    confidence FLOAT,
    cost_yen FLOAT DEFAULT 0,
    -- 検索用embedding（質問のベクトル、類似質問検索・クラスタリング用）
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- インデックス
CREATE INDEX idx_qa_sessions_company ON qa_sessions(company_id);
CREATE INDEX idx_qa_sessions_company_created ON qa_sessions(company_id, created_at DESC);
CREATE INDEX idx_qa_sessions_answer_status ON qa_sessions(company_id, answer_status);

-- RLS
ALTER TABLE qa_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY qa_sessions_select ON qa_sessions
    FOR SELECT USING (company_id = (current_setting('app.company_id', true))::uuid);

CREATE POLICY qa_sessions_insert ON qa_sessions
    FOR INSERT WITH CHECK (company_id = (current_setting('app.company_id', true))::uuid);


-- === 009_knowledge_bpo_fields.sql ===

-- knowledge_items に BPO関連フィールドとソースタグを追加
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS source_tag TEXT;          -- "common" or "construction" etc.
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS bpo_automatable BOOLEAN DEFAULT false;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS bpo_method TEXT;           -- SaaS名・自動化手法

CREATE INDEX IF NOT EXISTS idx_knowledge_items_bpo ON knowledge_items(company_id, bpo_automatable) WHERE bpo_automatable = true;


-- === 010_knowledge_session_files.sql ===

-- 010: knowledge_sessions にファイルメタデータカラム追加
-- ファイルアップロード時に元ファイルを Supabase Storage に保存し、
-- ナレッジ詳細画面でソースファイルの閲覧・削除・再アップロードを可能にする

ALTER TABLE knowledge_sessions
  ADD COLUMN IF NOT EXISTS file_name TEXT,
  ADD COLUMN IF NOT EXISTS file_size INTEGER,
  ADD COLUMN IF NOT EXISTS file_content_type TEXT,
  ADD COLUMN IF NOT EXISTS file_storage_path TEXT;

COMMENT ON COLUMN knowledge_sessions.file_name IS '元のファイル名';
COMMENT ON COLUMN knowledge_sessions.file_size IS 'ファイルサイズ(bytes)';
COMMENT ON COLUMN knowledge_sessions.file_content_type IS 'MIMEタイプ';
COMMENT ON COLUMN knowledge_sessions.file_storage_path IS 'Supabase Storage 内のパス (bucket: knowledge-files)';

-- Storage bucket はSupabase Dashboard or supabase CLI で作成:
-- supabase storage create knowledge-files --public=false


-- === 011_session_cost_tracking.sql ===

-- 011: knowledge_sessions に LLMコスト追跡カラム追加
-- 月間コスト集計を可能にする

ALTER TABLE knowledge_sessions
  ADD COLUMN IF NOT EXISTS cost_yen FLOAT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS model_used TEXT;

COMMENT ON COLUMN knowledge_sessions.cost_yen IS 'LLM利用コスト（円）';
COMMENT ON COLUMN knowledge_sessions.model_used IS '使用したLLMモデル名';

-- 月間集計用インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_company_created
  ON knowledge_sessions(company_id, created_at);


-- === 012_feedback_learning.sql ===

-- 012_feedback_learning.sql
-- フィードバック学習ループ Level 1
-- Source of Truth: shachotwo/d_02_フィードバック学習ループ設計.md
--
-- [衝突確認済み]
-- estimation_items: user_modified / original_ai_price / finalized_at → 新規追加OK
-- unit_price_master: ai_estimated_price / accuracy_rate / used_count → 新規追加OK
-- qa_sessions: user_rating は 008_qa_sessions.sql で TEXT 型定義済み → INTEGER追加は不可。
--              INTEGER評価は user_rating_score カラムとして追加。
--              user_feedback は 008 で定義済み → rating_comment はスキップ（同義カラム）。
--              source_item_ids → 新規追加OK
-- knowledge_items: extraction_modified / original_content → 新規追加OK
-- bpo_approvals: modification_diff / rejection_reason / learned_rule → 新規追加OK

-- =============================================================================
-- 1. 積算 単価フィードバック（estimation_items に追加）
-- =============================================================================
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS user_modified BOOLEAN DEFAULT false;
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS original_ai_price DECIMAL(15,2);
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ;

COMMENT ON COLUMN estimation_items.user_modified IS 'AIの積算をユーザーが修正したかどうか';
COMMENT ON COLUMN estimation_items.original_ai_price IS 'AI推定元の単価（修正前の値を保持）';
COMMENT ON COLUMN estimation_items.finalized_at IS '積算確定日時（確定後の変更を追跡）';

-- =============================================================================
-- 2. 単価マスタに精度追跡カラム追加（unit_price_master に追加）
-- =============================================================================
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS ai_estimated_price DECIMAL(15,2);
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS accuracy_rate DECIMAL(5,4);
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS used_count INTEGER DEFAULT 0;

COMMENT ON COLUMN unit_price_master.ai_estimated_price IS 'AIが推定した単価（実績単価との差分で精度を計算）';
COMMENT ON COLUMN unit_price_master.accuracy_rate IS 'AI推定精度（0.0000〜1.0000）。|実績-推定|/実績で算出';
COMMENT ON COLUMN unit_price_master.used_count IS 'この単価が積算に使われた回数（学習データ量の指標）';

-- =============================================================================
-- 3. 数量抽出フィードバック（新規テーブル）
-- =============================================================================
CREATE TABLE IF NOT EXISTS extraction_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    project_id UUID NOT NULL REFERENCES estimation_projects(id),
    original_items JSONB NOT NULL,
    corrected_items JSONB NOT NULL,
    diff_summary JSONB,
    source_format TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE extraction_feedback ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "extraction_feedback_company_isolation" ON extraction_feedback;
CREATE POLICY "extraction_feedback_company_isolation" ON extraction_feedback
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 4. 工種正規化辞書（新規テーブル）
-- company_id = NULL は全社共通エントリ（シャチョツー運営が管理するマスタ辞書）
-- =============================================================================
CREATE TABLE IF NOT EXISTS term_normalization (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    domain TEXT NOT NULL DEFAULT 'construction',
    original_term TEXT NOT NULL,
    normalized_term TEXT NOT NULL,
    occurrence_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(company_id, domain, original_term)
);

ALTER TABLE term_normalization ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "term_normalization_read" ON term_normalization;
CREATE POLICY "term_normalization_read" ON term_normalization
    FOR SELECT USING (
        company_id IS NULL
        OR company_id = current_setting('app.company_id', true)::uuid
    );

DROP POLICY IF EXISTS "term_normalization_write" ON term_normalization;
CREATE POLICY "term_normalization_write" ON term_normalization
    FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true)::uuid);

DROP POLICY IF EXISTS "term_normalization_update" ON term_normalization;
CREATE POLICY "term_normalization_update" ON term_normalization
    FOR UPDATE USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 5. Q&A フィードバック（qa_sessions に追加）
-- 注意: user_rating (TEXT) は 008 で定義済み。数値スコアは user_rating_score として追加。
--       user_feedback (TEXT) も 008 で定義済みのため rating_comment は追加しない。
-- =============================================================================
ALTER TABLE qa_sessions ADD COLUMN IF NOT EXISTS user_rating_score INTEGER
    CHECK (user_rating_score IS NULL OR (user_rating_score >= 1 AND user_rating_score <= 5));
ALTER TABLE qa_sessions ADD COLUMN IF NOT EXISTS source_item_ids UUID[];

COMMENT ON COLUMN qa_sessions.user_rating_score IS '数値評価（1=最低〜5=最高）。user_rating(TEXT)と併用';
COMMENT ON COLUMN qa_sessions.source_item_ids IS '回答生成に参照したknowledge_itemsのID群';

-- =============================================================================
-- 6. ナレッジ抽出フィードバック（knowledge_items に追加）
-- =============================================================================
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS extraction_modified BOOLEAN DEFAULT false;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS original_content JSONB;

COMMENT ON COLUMN knowledge_items.extraction_modified IS 'LLM抽出結果をユーザーが編集したかどうか';
COMMENT ON COLUMN knowledge_items.original_content IS 'LLM抽出時の元コンテンツJSON（編集前の状態を保持）';

-- =============================================================================
-- 7. BPO承認フィードバック（bpo_approvals に追加）
-- =============================================================================
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS modification_diff JSONB;
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS learned_rule TEXT;

COMMENT ON COLUMN bpo_approvals.modification_diff IS '承認者が内容を修正した場合の差分JSON（before/after）';
COMMENT ON COLUMN bpo_approvals.rejection_reason IS '却下理由（フィードバック学習の入力として使用）';
COMMENT ON COLUMN bpo_approvals.learned_rule IS '却下・修正パターンから抽出した学習ルールのテキスト';

-- =============================================================================
-- インデックス
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_extraction_feedback_company ON extraction_feedback(company_id);
CREATE INDEX IF NOT EXISTS idx_extraction_feedback_project ON extraction_feedback(project_id);
CREATE INDEX IF NOT EXISTS idx_term_normalization_lookup ON term_normalization(company_id, domain, original_term);
CREATE INDEX IF NOT EXISTS idx_unit_price_master_accuracy ON unit_price_master(company_id, category, accuracy_rate);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_rating_score ON qa_sessions(company_id, user_rating_score)
    WHERE user_rating_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_items_extraction_modified ON knowledge_items(company_id)
    WHERE extraction_modified = true;


-- === 013_llm_call_logs.sql ===

-- 013_llm_call_logs.sql
-- LLM呼び出しログ — 成功・失敗両方を記録し、精度改善のPDCAを回す
-- Source of Truth: shachotwo/d_02_フィードバック学習ループ設計.md

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    task_type TEXT NOT NULL,                    -- quantity_extraction / price_estimation / qa / etc.
    model_used TEXT,                            -- gemini-2.5-flash / claude-sonnet-4-5 etc.
    input_text_length INTEGER,                  -- 入力テキストの長さ
    input_summary TEXT,                         -- 入力の要約（最初の200文字等）
    output_text_length INTEGER,                 -- 出力テキストの長さ
    output_summary TEXT,                        -- 出力の要約（最初の200文字等）
    status TEXT NOT NULL DEFAULT 'success'
        CHECK (status IN ('success', 'parse_error', 'partial_recovery', 'api_error', 'safety_block', 'timeout')),
    items_extracted INTEGER,                    -- 抽出件数（extraction系の場合）
    parse_method TEXT,                          -- direct / code_block / bracket_extract / partial_recovery
    error_message TEXT,                         -- エラーメッセージ
    raw_response TEXT,                          -- LLM生レスポンス（デバッグ用。長期保存しない）
    latency_ms INTEGER,                         -- レスポンス時間
    cost_yen DECIMAL(10,4),                     -- 推定コスト
    project_id UUID,                            -- 関連するプロジェクトID（あれば）
    metadata JSONB DEFAULT '{}',                -- その他メタデータ
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLSは company_id が NULL の場合もあるため条件付き
ALTER TABLE llm_call_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "llm_call_logs_read" ON llm_call_logs
    FOR SELECT USING (
        company_id IS NULL
        OR company_id = current_setting('app.company_id', true)::uuid
    );
CREATE POLICY "llm_call_logs_insert" ON llm_call_logs
    FOR INSERT WITH CHECK (true);  -- 全社insertは許可（ログ記録のため）

-- インデックス
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_company ON llm_call_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_task ON llm_call_logs(task_type, status);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_created ON llm_call_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_errors ON llm_call_logs(task_type)
    WHERE status != 'success';


-- === 014_bpo_manufacturing.sql ===

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


-- === 015_execution_hitl.sql ===

-- Migration 015: BPO Human-in-the-Loop承認フロー
-- ADR-009に基づく: パイロット前にHitL必須化
-- 金額・外部送付を伴うBPO実行を必ず人間が確認してから実行する

-- execution_logsにHitL管理カラムを追加
ALTER TABLE execution_logs
    ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (approval_status IN ('pending', 'approved', 'rejected', 'modified')),
    ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS original_output JSONB,
    ADD COLUMN IF NOT EXISTS modified_output JSONB;

-- HitL必須パイプラインの定義テーブル
CREATE TABLE IF NOT EXISTS bpo_hitl_requirements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_key TEXT NOT NULL UNIQUE,  -- 例: "construction/estimation"
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    min_confidence_for_auto FLOAT,      -- この信頼度以上なら自動承認OK（NULLは常にHitL）
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 金額・外部送付を伴うパイプラインを登録（全て常にHitL必須）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('construction/estimation',  TRUE, NULL, '建設業見積書: 金額誤りリスク高'),
    ('construction/billing',     TRUE, NULL, '建設業請求書: 外部送付'),
    ('construction/safety',      FALSE, 0.95, '安全書類: 低リスクのため高信頼度で自動可'),
    ('manufacturing/estimation', TRUE, NULL, '製造業見積: 金額誤りリスク高'),
    ('common/expense',           TRUE, NULL, '経費精算: 金額・外部処理'),
    ('common/payroll',           TRUE, NULL, '給与計算: 金額・機密情報'),
    ('common/contract',          TRUE, NULL, '契約書: 法的効力あり')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval = EXCLUDED.requires_approval,
    min_confidence_for_auto = EXCLUDED.min_confidence_for_auto,
    description = EXCLUDED.description;

-- 承認待ちアイテムを高速に取得するインデックス
CREATE INDEX IF NOT EXISTS idx_execution_logs_pending
    ON execution_logs (company_id, approval_status, created_at DESC)
    WHERE approval_status = 'pending';

-- RLS: execution_logsの承認操作は同一テナントのadminのみ
-- (既存のRLSに追加条件として適用)
COMMENT ON COLUMN execution_logs.approval_status IS
    'HitL承認状態: pending=承認待ち, approved=承認済, rejected=却下, modified=修正して承認';

COMMENT ON COLUMN execution_logs.original_output IS
    '承認前のパイプライン出力（修正比較・監査用）';


-- === 016_knowledge_half_life.sql ===

-- Migration 016: ナレッジ半減期・精度フィードバック
-- ADR-010に基づく: 長期運用でナレッジが陳腐化しないための基盤
-- Phase 1: 手動TTL設定のみ。Phase 2: 自動圧縮バッチに接続

-- knowledge_itemsに半減期・フィードバックカラムを追加
ALTER TABLE knowledge_items
    ADD COLUMN IF NOT EXISTS half_life_days INT DEFAULT NULL,
        -- NULL = 期限なし。30 = 30日で要確認フラグ
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT NULL,
        -- half_life_daysから自動計算。NULLは永続
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ DEFAULT NULL,
        -- 管理者が「この情報は正確です」と確認した日時
    ADD COLUMN IF NOT EXISTS qa_usage_count INT NOT NULL DEFAULT 0,
        -- このナレッジがQ&Aで引用された回数（重要度指標）
    ADD COLUMN IF NOT EXISTS positive_feedback_count INT NOT NULL DEFAULT 0,
        -- Q&A回答後にユーザーが「役立った」と評価した回数
    ADD COLUMN IF NOT EXISTS negative_feedback_count INT NOT NULL DEFAULT 0;
        -- Q&A回答後にユーザーが「役立たなかった」と評価した回数

-- 期限切れナレッジの高速検索インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_items_expires
    ON knowledge_items (company_id, expires_at)
    WHERE expires_at IS NOT NULL AND is_active = TRUE;

-- よく使われる（高価値な）ナレッジの検索インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_items_usage
    ON knowledge_items (company_id, qa_usage_count DESC)
    WHERE is_active = TRUE;

-- Q&Aセッションにフィードバックカラムを追加
ALTER TABLE qa_sessions
    ADD COLUMN IF NOT EXISTS feedback TEXT
        CHECK (feedback IN ('helpful', 'not_helpful', NULL)),
    ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cited_knowledge_ids UUID[];
        -- この回答で実際に引用されたナレッジのID一覧

COMMENT ON COLUMN knowledge_items.half_life_days IS
    'ナレッジの有効期間（日数）。毎月更新が必要な単価情報=30, 年次更新=365, 変わらないルール=NULL';

COMMENT ON COLUMN knowledge_items.qa_usage_count IS
    'このナレッジがQ&A検索のtop-kに含まれた回数。使われないナレッジは精度評価の対象外';


-- === 017_qa_usage_rpc.sql ===

-- Migration 017: qa_usage_count インクリメント用RPC関数
-- brain/knowledge/qa.py から呼び出される。
-- Q&A検索で引用されたナレッジのqa_usage_countを一括インクリメントする。

CREATE OR REPLACE FUNCTION increment_qa_usage_count(item_ids UUID[])
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE knowledge_items
    SET qa_usage_count = qa_usage_count + 1
    WHERE id = ANY(item_ids)
      AND is_active = TRUE;
END;
$$;

-- RLSをバイパスするためSECURITY DEFINERを使用。
-- 呼び出し元（brain/knowledge/qa.py）がservice_roleクライアント経由でのみ呼び出す。
COMMENT ON FUNCTION increment_qa_usage_count(UUID[]) IS
    'Q&A検索で引用されたナレッジのqa_usage_countを一括インクリメントする。brain/knowledge/qa.pyから呼び出し。';


-- === 018_bpo_hitl_all_pipelines.sql ===

-- Migration 018: bpo_hitl_requirements 全29パイプライン登録
-- Migration 015 で登録済みの7本に加え、残り22本を追加する。
-- pipeline_key は workers/bpo/{industry}/pipelines/{name}_pipeline.py のパスに対応。

-- 建設業: 追加4本
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('construction/photo_organize', FALSE, 0.85, '建設業施工写真整理: 外部送付なし・低リスク'),
    ('construction/cost_report',    TRUE,  NULL,  '建設業原価報告: 金額・経営情報'),
    ('construction/subcontractor',  TRUE,  NULL,  '建設業下請管理: 金額・法的書類'),
    ('construction/permit',         TRUE,  NULL,  '建設業許可申請: 法的効力あり')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 製造業: 実際のpipeline_keyを登録（015は manufacturing/estimation だったが
--   正確なキーは manufacturing/quoting に合わせて追加）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('manufacturing/quoting', TRUE, NULL, '製造業見積: 金額誤りリスク高（manufacturing/estimationと同義）')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 共通BPO: 残り3本
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('common/attendance',      TRUE,  NULL,  '勤怠集計: 給与計算に直結・機密情報'),
    ('common/vendor',          FALSE, 0.90,  '取引先マスタ更新: 低リスク・高信頼度で自動可'),
    ('common/admin_reminder',  FALSE, 0.80,  '管理リマインダ: 通知のみ・低リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 飲食業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('restaurant/fl_cost', TRUE,  NULL,  '飲食業FL原価計算: 仕入金額・経営情報'),
    ('restaurant/shift',   FALSE, 0.85,  '飲食業シフト生成: 外部送付なし・低リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 美容業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('beauty/recall', FALSE, 0.80, '美容リコール送信: 顧客連絡・文面要確認のため中程度')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 人材派遣業
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('staffing/dispatch_contract', TRUE, NULL, '派遣契約書作成: 法的効力・個人情報含む')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 設計事務所（建築）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('architecture/building_permit', TRUE, NULL, '建築確認申請: 法的書類・許可申請')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 医療クリニック（法的文書整備後に解禁）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('clinic/medical_receipt', TRUE, NULL, '医療レセプト: 要配慮個人情報・法的書類整備後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- ホテル
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('hotel/revenue_mgmt', TRUE, NULL, 'ホテル収益管理: 料金設定・外部OTA連携')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 調剤薬局（法的文書整備後に解禁）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('pharmacy/dispensing_billing', TRUE, NULL, '薬局調剤報酬請求: 要配慮個人情報・法的書類整備後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 歯科（パイロット解禁条件クリア後のみ使用可）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('dental/receipt_check', TRUE, NULL, '歯科レセプト点検: 要配慮個人情報・3省2ガイドライン準拠後のみ解禁')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 不動産
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('realestate/rent_collection', TRUE, NULL, '不動産家賃管理: 金額・入金確認・外部連絡')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 士業（守秘義務あり）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('professional/deadline_mgmt', TRUE, NULL, '士業期限管理: 法的期限・重大なミスリスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 介護
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('nursing/care_billing', TRUE, NULL, '介護報酬請求: 要配慮個人情報・請求金額')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 物流
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('logistics/dispatch', FALSE, 0.88, '物流配車最適化: 外部送付あり・中程度リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- EC（電子商取引）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('ecommerce/listing', FALSE, 0.85, 'EC商品登録: 外部公開前に確認推奨・中程度リスク')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 自動車整備
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('auto_repair/repair_quoting', TRUE, NULL, '自動車整備見積: 金額・顧客への説明責任')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval        = EXCLUDED.requires_approval,
    min_confidence_for_auto  = EXCLUDED.min_confidence_for_auto,
    description              = EXCLUDED.description;

-- 登録件数確認用コメント
-- 015 登録済み: 7本（construction×3, manufacturing/estimation, common×3）
-- 018 追加: 22本
-- 合計: 29本（全パイプライン対応完了）

COMMENT ON TABLE bpo_hitl_requirements IS
    'BPOパイプライン別のHuman-in-the-Loop承認要否設定。全29パイプライン登録済み（018完了）。';


-- === 019_knowledge_feedback_rpc.sql ===

-- Migration 019: knowledge_items フィードバックカウント インクリメント用 RPC
-- brain/knowledge/qa.py のフィードバック評価（👍👎）から呼び出される。
-- positive_feedback_count または negative_feedback_count を一括インクリメントする。

CREATE OR REPLACE FUNCTION increment_knowledge_feedback(
    item_ids   UUID[],
    count_column TEXT  -- 'positive_feedback_count' or 'negative_feedback_count'
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- count_column の値を検証してSQLインジェクションを防ぐ
    IF count_column NOT IN ('positive_feedback_count', 'negative_feedback_count') THEN
        RAISE EXCEPTION 'Invalid count_column: %', count_column;
    END IF;

    IF count_column = 'positive_feedback_count' THEN
        UPDATE knowledge_items
        SET positive_feedback_count = positive_feedback_count + 1
        WHERE id = ANY(item_ids)
          AND is_active = TRUE;
    ELSE
        UPDATE knowledge_items
        SET negative_feedback_count = negative_feedback_count + 1
        WHERE id = ANY(item_ids)
          AND is_active = TRUE;
    END IF;
END;
$$;

-- RLSをバイパスするためSECURITY DEFINERを使用。
-- 呼び出し元（routers/knowledge.py）がservice_roleクライアント経由でのみ呼び出す。
COMMENT ON FUNCTION increment_knowledge_feedback(UUID[], TEXT) IS
    'Q&A評価で引用されたナレッジのpositive/negative_feedback_countを一括インクリメントする。routers/knowledge.pyから呼び出し。';


-- === 020_mfg_quoting_engine.sql ===

-- 020: 製造業見積3層エンジン対応
-- mfg_quotesに業種・レイヤー情報を追加

ALTER TABLE mfg_quotes
  ADD COLUMN IF NOT EXISTS sub_industry TEXT DEFAULT 'metalwork',
  ADD COLUMN IF NOT EXISTS layers_used JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS overall_confidence DECIMAL(3,2),
  ADD COLUMN IF NOT EXISTS additional_costs JSONB DEFAULT '[]';

-- mfg_quote_itemsにレイヤーソース追加
ALTER TABLE mfg_quote_items
  ADD COLUMN IF NOT EXISTS layer_source TEXT DEFAULT 'yaml';

-- user_modifiedカラムが存在しない場合に追加
ALTER TABLE mfg_quote_items
  ADD COLUMN IF NOT EXISTS user_modified BOOLEAN DEFAULT false;

-- 学習ループ用の集約ビュー
CREATE OR REPLACE VIEW mfg_historical_averages AS
SELECT
  company_id,
  equipment_type,
  COUNT(*) as sample_count,
  AVG(setup_time_min) FILTER (WHERE user_modified = true) as avg_setup_min,
  AVG(cycle_time_min) FILTER (WHERE user_modified = true) as avg_cycle_min,
  AVG(confidence) as avg_confidence
FROM mfg_quote_items
WHERE user_modified = true
  AND created_at > now() - interval '6 months'
GROUP BY company_id, equipment_type
HAVING COUNT(*) >= 3;

-- インデックス
CREATE INDEX IF NOT EXISTS idx_mfg_quotes_sub_industry
  ON mfg_quotes(company_id, sub_industry);
CREATE INDEX IF NOT EXISTS idx_mfg_quote_items_modified
  ON mfg_quote_items(company_id, equipment_type, user_modified)
  WHERE user_modified = true;


-- === 021_sfa_crm_cs_tables.sql ===

-- 021_sfa_crm_cs_tables.sql
-- SFA・CRM・CS テーブル群
-- Source of Truth: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md §3.2

-- =============================================================================
-- SFA テーブル
-- =============================================================================

-- 1. leads — リード管理
-- =============================================================================
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),

    -- リード情報
    company_name TEXT NOT NULL,               -- 見込み企業名
    contact_name TEXT,                        -- 担当者名
    contact_email TEXT,                       -- メールアドレス
    contact_phone TEXT,                       -- 電話番号
    industry TEXT,                            -- 業種（16業種コード）
    employee_count INTEGER,                   -- 従業員数

    -- ソース・スコア
    source TEXT NOT NULL,                     -- 流入元: website / referral / event / outbound
    source_detail TEXT,                       -- 詳細（フォームURL、紹介者名等）
    score INTEGER DEFAULT 0,                  -- AIスコア（0-100）
    score_reasons JSONB DEFAULT '[]',         -- スコア根拠

    -- ステータス
    status TEXT NOT NULL DEFAULT 'new',       -- new / contacted / qualified / unqualified / nurturing
    assigned_to UUID REFERENCES users(id),    -- 担当者（NULLならAI自動対応）

    -- タイムスタンプ
    first_contact_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 2. lead_activities — リード行動ログ
-- =============================================================================
CREATE TABLE lead_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

    activity_type TEXT NOT NULL,              -- page_view / form_submit / email_open / email_click / meeting / call
    activity_data JSONB DEFAULT '{}',         -- 行動詳細
    channel TEXT,                             -- web / email / slack / phone

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 3. opportunities — 商談管理
-- =============================================================================
CREATE TABLE opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    lead_id UUID REFERENCES leads(id),

    -- 商談情報
    title TEXT NOT NULL,                      -- 商談名
    target_company_name TEXT NOT NULL,        -- 対象企業名
    target_industry TEXT,                     -- 対象業種

    -- 金額・モジュール
    selected_modules JSONB NOT NULL DEFAULT '[]',  -- ["brain", "bpo_core", "bpo_additional_1"]
    monthly_amount INTEGER NOT NULL DEFAULT 0,      -- 月額合計（円）
    annual_amount INTEGER GENERATED ALWAYS AS (monthly_amount * 12) STORED,

    -- パイプライン
    stage TEXT NOT NULL DEFAULT 'proposal',   -- proposal / quotation / negotiation / contract / won / lost
    probability INTEGER DEFAULT 50,           -- 受注確度（%）
    expected_close_date DATE,                 -- 受注予定日
    lost_reason TEXT,                         -- 失注理由

    -- タイムスタンプ
    stage_changed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 4. proposals — 提案書
-- =============================================================================
CREATE TABLE proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id),

    version INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    content JSONB NOT NULL,                   -- 提案書構造化データ
    pdf_storage_path TEXT,                    -- Supabase Storage パス

    -- 送付
    sent_at TIMESTAMPTZ,
    sent_to TEXT,                             -- 送付先メール
    opened_at TIMESTAMPTZ,                   -- 開封日時

    status TEXT NOT NULL DEFAULT 'draft',     -- draft / sent / viewed / accepted / rejected
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 5. quotations — 見積書
-- =============================================================================
CREATE TABLE quotations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id),

    version INTEGER NOT NULL DEFAULT 1,
    quotation_number TEXT NOT NULL,           -- 見積番号（自動採番）

    -- 明細
    line_items JSONB NOT NULL,               -- [{module, unit_price, quantity, subtotal}]
    subtotal INTEGER NOT NULL,               -- 小計
    tax INTEGER NOT NULL,                    -- 消費税
    total INTEGER NOT NULL,                  -- 合計
    valid_until DATE NOT NULL,               -- 有効期限

    -- 送付
    pdf_storage_path TEXT,
    sent_at TIMESTAMPTZ,
    sent_to TEXT,

    status TEXT NOT NULL DEFAULT 'draft',     -- draft / sent / accepted / rejected / expired
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 6. contracts — 契約書
-- =============================================================================
CREATE TABLE contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id),

    contract_number TEXT NOT NULL,            -- 契約番号
    contract_type TEXT NOT NULL DEFAULT 'subscription',  -- subscription / one_time

    -- 契約内容
    selected_modules JSONB NOT NULL,
    monthly_amount INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,                            -- NULL = 自動更新
    auto_renew BOOLEAN DEFAULT TRUE,

    -- 電子署名
    signing_service TEXT DEFAULT 'cloudsign',  -- cloudsign / docusign
    signing_request_id TEXT,                   -- 外部署名サービスID
    signed_at TIMESTAMPTZ,
    pdf_storage_path TEXT,

    status TEXT NOT NULL DEFAULT 'draft',      -- draft / sent / signed / active / terminated
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- CRM テーブル
-- =============================================================================

-- 7. customers — 顧客管理（契約締結後にleadsから昇格）
-- =============================================================================
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    lead_id UUID REFERENCES leads(id),

    -- 企業情報
    customer_company_name TEXT NOT NULL,
    industry TEXT NOT NULL,
    employee_count INTEGER,

    -- 契約情報
    plan TEXT NOT NULL,                       -- brain / bpo_core / enterprise
    active_modules JSONB NOT NULL DEFAULT '[]',
    mrr INTEGER NOT NULL DEFAULT 0,           -- 月次経常収益

    -- ヘルス
    health_score INTEGER DEFAULT 100,         -- 0-100
    nps_score INTEGER,                        -- -100〜100
    last_nps_at TIMESTAMPTZ,

    -- ステータス
    status TEXT NOT NULL DEFAULT 'onboarding', -- onboarding / active / at_risk / churned
    onboarded_at TIMESTAMPTZ,
    churned_at TIMESTAMPTZ,
    churn_reason TEXT,

    -- 担当
    cs_owner UUID REFERENCES users(id),       -- カスタマーサクセス担当

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 8. customer_health — 顧客ヘルススコア履歴
-- =============================================================================
CREATE TABLE customer_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    customer_id UUID NOT NULL REFERENCES customers(id),

    score INTEGER NOT NULL,                   -- 0-100
    dimensions JSONB NOT NULL,                -- {usage: 80, engagement: 70, support: 90, nps: 60, expansion: 50}
    risk_factors JSONB DEFAULT '[]',          -- ["低ログイン頻度", "未回答NPS"]

    calculated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 9. revenue_records — 売上記録
-- =============================================================================
CREATE TABLE revenue_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    customer_id UUID NOT NULL REFERENCES customers(id),

    record_type TEXT NOT NULL,                -- mrr / expansion / contraction / churn
    amount INTEGER NOT NULL,                  -- 金額（円）
    modules JSONB,                            -- 対象モジュール
    effective_date DATE NOT NULL,

    -- freee連携
    freee_invoice_id INTEGER,                -- freee請求書ID
    payment_status TEXT DEFAULT 'pending',    -- pending / paid / overdue / failed

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 10. feature_requests — 要望管理
-- =============================================================================
CREATE TABLE feature_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    customer_id UUID NOT NULL REFERENCES customers(id),

    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT,                             -- feature / improvement / integration / bug
    priority TEXT DEFAULT 'medium',           -- low / medium / high / critical

    -- AIによる分類・集約
    ai_category JSONB,                        -- AI自動分類タグ
    similar_request_ids UUID[],               -- 類似要望のID
    vote_count INTEGER DEFAULT 1,             -- 同様要望のカウント

    status TEXT NOT NULL DEFAULT 'new',       -- new / reviewing / planned / in_progress / done / declined
    response TEXT,                            -- 回答内容
    responded_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- CS テーブル
-- =============================================================================

-- 11. support_tickets — サポートチケット
-- =============================================================================
CREATE TABLE support_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    customer_id UUID NOT NULL REFERENCES customers(id),

    ticket_number TEXT NOT NULL,              -- チケット番号（自動採番）
    subject TEXT NOT NULL,

    -- 分類
    category TEXT NOT NULL,                   -- usage / billing / bug / feature / account
    priority TEXT NOT NULL DEFAULT 'medium',  -- low / medium / high / urgent

    -- AI対応
    ai_handled BOOLEAN DEFAULT FALSE,         -- AI自動対応済みか
    ai_confidence FLOAT,                      -- AI回答の確信度
    ai_response TEXT,                         -- AI生成回答

    -- エスカレーション
    escalated BOOLEAN DEFAULT FALSE,
    escalated_to UUID REFERENCES users(id),
    escalation_reason TEXT,

    -- SLA
    sla_due_at TIMESTAMPTZ,                  -- SLA期限
    first_response_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,

    status TEXT NOT NULL DEFAULT 'open',      -- open / waiting / ai_responded / escalated / resolved / closed
    satisfaction_score INTEGER,               -- 1-5

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 12. ticket_messages — チケットメッセージ
-- =============================================================================
CREATE TABLE ticket_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    ticket_id UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,

    sender_type TEXT NOT NULL,                -- customer / agent / ai
    sender_id UUID,                           -- users.id or NULL(AI)
    content TEXT NOT NULL,
    attachments JSONB DEFAULT '[]',

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 後方参照FK（customersテーブル作成後に追加）
-- =============================================================================
ALTER TABLE opportunities ADD COLUMN customer_id UUID REFERENCES customers(id);

-- =============================================================================
-- インデックス
-- =============================================================================

-- SFA
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(company_id, status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(company_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_lead_activities_lead ON lead_activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_activities_company ON lead_activities(company_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_company ON opportunities(company_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities(company_id, stage);
CREATE INDEX IF NOT EXISTS idx_proposals_opportunity ON proposals(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_quotations_opportunity ON quotations(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_contracts_opportunity ON contracts(opportunity_id);

-- CRM
CREATE INDEX IF NOT EXISTS idx_customers_company ON customers(company_id);
CREATE INDEX IF NOT EXISTS idx_customers_health ON customers(company_id, health_score);
CREATE INDEX IF NOT EXISTS idx_customers_status ON customers(company_id, status);
CREATE INDEX IF NOT EXISTS idx_customer_health_customer ON customer_health(customer_id);
CREATE INDEX IF NOT EXISTS idx_revenue_records_customer ON revenue_records(customer_id);
CREATE INDEX IF NOT EXISTS idx_revenue_records_date ON revenue_records(company_id, effective_date);
CREATE INDEX IF NOT EXISTS idx_feature_requests_customer ON feature_requests(customer_id);
CREATE INDEX IF NOT EXISTS idx_feature_requests_votes ON feature_requests(company_id, vote_count DESC);

-- CS
CREATE INDEX IF NOT EXISTS idx_support_tickets_company ON support_tickets(company_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(company_id, status);
CREATE INDEX IF NOT EXISTS idx_support_tickets_sla ON support_tickets(sla_due_at) WHERE status != 'closed';
CREATE INDEX IF NOT EXISTS idx_support_tickets_customer ON support_tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket ON ticket_messages(ticket_id);

-- =============================================================================
-- RLS（将来用 — パイロット後に有効化）
-- =============================================================================
-- 全テーブルに company_id ベースの RLS を適用する。
-- パターン:
--   ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY "tenant_isolation" ON {table}
--     USING (company_id = current_setting('app.company_id', true)::uuid);
--
-- 対象テーブル:
--   leads, lead_activities, opportunities, proposals, quotations, contracts,
--   customers, customer_health, revenue_records, feature_requests,
--   support_tickets, ticket_messages


-- === 022_learning_tables.sql ===

-- 022_learning_tables.sql
-- 学習フィードバックループ テーブル群
-- Source of Truth: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md §4.10

-- =============================================================================
-- 1. win_loss_patterns — 受注/失注パターン（学習データ）
-- =============================================================================
CREATE TABLE win_loss_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id),

    outcome TEXT NOT NULL,                    -- won / lost
    industry TEXT,                            -- 業種
    employee_range TEXT,                      -- 規模帯
    lead_source TEXT,                         -- リードソース
    sales_cycle_days INTEGER,                 -- セールスサイクル日数
    selected_modules JSONB,                   -- 選択モジュール
    lost_reason TEXT,                         -- 失注理由（lostの場合）
    win_factors JSONB,                        -- 受注要因（wonの場合）
    proposal_version_id UUID,                 -- 使用した提案書

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 2. outreach_performance — アウトリーチ成果（PDCA用）
-- =============================================================================
CREATE TABLE outreach_performance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),

    period DATE NOT NULL,                     -- 集計期間（日次）
    industry TEXT NOT NULL,                   -- 業種

    -- 漏斗指標
    researched_count INTEGER DEFAULT 0,       -- リサーチ数
    outreached_count INTEGER DEFAULT 0,       -- アウトリーチ数
    lp_viewed_count INTEGER DEFAULT 0,        -- LP閲覧数
    lead_converted_count INTEGER DEFAULT 0,   -- リード化数
    meeting_booked_count INTEGER DEFAULT 0,   -- 商談予約数

    -- メールA/Bテスト
    email_variant TEXT,                       -- メールバリアント名
    open_rate FLOAT,                          -- 開封率
    click_rate FLOAT,                         -- クリック率

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 3. cs_feedback — CS学習データ
-- =============================================================================
CREATE TABLE cs_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    ticket_id UUID NOT NULL REFERENCES support_tickets(id),

    ai_response TEXT,                         -- AI生成回答
    human_correction TEXT,                    -- 人間による修正（あれば）
    csat_score INTEGER,                       -- 顧客満足度
    was_escalated BOOLEAN DEFAULT FALSE,

    -- 学習判定
    quality_label TEXT,                       -- good / needs_improvement / bad
    improvement_applied BOOLEAN DEFAULT FALSE, -- FAQに反映済みか

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 4. scoring_model_versions — スコアリングモデルバージョン管理
-- =============================================================================
CREATE TABLE scoring_model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),

    model_type TEXT NOT NULL,                 -- lead_score / health_score / upsell_timing
    version INTEGER NOT NULL,
    weights JSONB NOT NULL,                   -- スコアリング重み
    performance_metrics JSONB,                -- 精度指標（適合率・再現率等）

    active BOOLEAN DEFAULT FALSE,             -- 現在使用中か
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- インデックス
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_win_loss_company ON win_loss_patterns(company_id);
CREATE INDEX IF NOT EXISTS idx_win_loss_industry ON win_loss_patterns(industry, outcome);
CREATE INDEX IF NOT EXISTS idx_win_loss_opportunity ON win_loss_patterns(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_outreach_perf_company ON outreach_performance(company_id);
CREATE INDEX IF NOT EXISTS idx_outreach_perf ON outreach_performance(period, industry);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_company ON cs_feedback(company_id);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_ticket ON cs_feedback(ticket_id);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_quality ON cs_feedback(quality_label);
CREATE INDEX IF NOT EXISTS idx_scoring_model_company ON scoring_model_versions(company_id);
CREATE INDEX IF NOT EXISTS idx_scoring_model_active ON scoring_model_versions(company_id, model_type) WHERE active = TRUE;

-- =============================================================================
-- RLS（将来用 — パイロット後に有効化）
-- =============================================================================
-- 全テーブルに company_id ベースの RLS を適用する。
-- パターン:
--   ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY "tenant_isolation" ON {table}
--     USING (company_id = current_setting('app.company_id', true)::uuid);
--
-- 対象テーブル:
--   win_loss_patterns, outreach_performance, cs_feedback, scoring_model_versions


-- === 023_pricing_tables.sql ===

-- 023: 料金マスタテーブル
-- ハードコードされた料金をDB管理に移行する

-- モジュール料金マスタ
CREATE TABLE IF NOT EXISTS pricing_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    module_code TEXT NOT NULL,          -- "brain" | "bpo_core" | "additional" | "backoffice"
    name TEXT NOT NULL,                  -- 表示名
    monthly_price INT NOT NULL,          -- 月額料金（税抜）
    description TEXT,                    -- モジュール説明
    is_active BOOLEAN DEFAULT TRUE,
    valid_from DATE DEFAULT CURRENT_DATE,
    valid_to DATE,                       -- NULL = 無期限
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 割引ルールマスタ
CREATE TABLE IF NOT EXISTS pricing_discounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    discount_code TEXT NOT NULL,          -- "annual" | "referral" | "volume" | "early_bird"
    name TEXT NOT NULL,                  -- 表示名
    discount_type TEXT NOT NULL DEFAULT 'rate',  -- "rate" (%) | "fixed" (円)
    rate_percent NUMERIC(5,2),           -- 割引率（%）
    fixed_amount INT,                    -- 固定割引額（円）
    conditions JSONB DEFAULT '{}',       -- 適用条件 {"min_modules": 3, "billing_cycle": "annual"}
    is_active BOOLEAN DEFAULT TRUE,
    valid_from DATE DEFAULT CURRENT_DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS ポリシー
ALTER TABLE pricing_modules ENABLE ROW LEVEL SECURITY;
ALTER TABLE pricing_discounts ENABLE ROW LEVEL SECURITY;

CREATE POLICY pricing_modules_tenant ON pricing_modules
    USING (company_id = current_setting('app.company_id', true)::UUID);

CREATE POLICY pricing_discounts_tenant ON pricing_discounts
    USING (company_id = current_setting('app.company_id', true)::UUID);

-- インデックス
CREATE INDEX idx_pricing_modules_company ON pricing_modules (company_id, is_active);
CREATE INDEX idx_pricing_discounts_company ON pricing_discounts (company_id, is_active);

-- 初期データ投入（デフォルト料金）
-- NOTE: company_id は実行時に置換する。ここでは system 用のプレースホルダー。
-- 実際のセットアップは startup スクリプトで行う。


-- === 024_onboarding_plan.sql ===

-- Migration 024: オンボーディング3プラン対応
-- companies テーブルに onboarding_plan / onboarding_steps カラムを追加

ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS onboarding_plan TEXT DEFAULT 'self'
    CHECK (onboarding_plan IN ('self', 'consul', 'full_support')),
  ADD COLUMN IF NOT EXISTS onboarding_steps JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN companies.onboarding_plan IS 'オンボーディングプラン: self / consul / full_support';
COMMENT ON COLUMN companies.onboarding_steps IS '手動完了ステップの記録 (JSON: {step_key: {completed: bool, completed_at, notes}})';


-- === 025_gws_sync_tables.sql ===

-- 025: Google Workspace 双方向同期基盤テーブル
-- watch_channels: Gmail/Calendar Watch APIチャネル管理
-- gws_sync_state: DB→GWS逆同期の冪等性管理

-- =====================================================================
-- watch_channels — Gmail Watch / Calendar Watch のチャネル管理
-- =====================================================================
CREATE TABLE IF NOT EXISTS watch_channels (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    service         TEXT NOT NULL CHECK (service IN ('gmail', 'calendar')),
    channel_id      TEXT NOT NULL UNIQUE,
    resource_id     TEXT,                   -- Google API が返す resourceId
    history_id      TEXT,                   -- Gmail: 最後に処理した historyId
    calendar_id     TEXT,                   -- Calendar の場合のみ (例: "primary")
    expiration      TIMESTAMPTZ NOT NULL,   -- Watch の有効期限
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watch_channels_company
    ON watch_channels(company_id);
CREATE INDEX IF NOT EXISTS idx_watch_channels_expiration
    ON watch_channels(expiration) WHERE is_active = true;

ALTER TABLE watch_channels ENABLE ROW LEVEL SECURITY;
CREATE POLICY watch_channels_tenant_isolation ON watch_channels
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- gws_sync_state — パイプライン結果→GWS反映の冪等性管理
-- =====================================================================
CREATE TABLE IF NOT EXISTS gws_sync_state (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sync_id         TEXT NOT NULL UNIQUE,    -- execution_log_id + sync_type のハッシュ
    sync_type       TEXT NOT NULL,           -- 'sheets' | 'calendar' | 'drive' | 'gmail_draft'
    source_pipeline TEXT NOT NULL,           -- 元パイプライン名
    target_resource TEXT,                    -- spreadsheet_id / calendar_id / folder_id 等
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'synced', 'failed', 'skipped')),
    last_synced_at  TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gws_sync_state_pending
    ON gws_sync_state(company_id, status) WHERE status = 'pending';

ALTER TABLE gws_sync_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY gws_sync_state_tenant_isolation ON gws_sync_state
    USING (company_id = current_setting('app.company_id')::uuid);


-- === 026_lead_enrichment_fields.sql ===

-- Migration 026: リード企業詳細情報・セグメント分類カラム追加
-- 製造業ターゲットリスト取得・セグメント分類機能に対応

-- リード企業の詳細情報（gBizINFO等で取得）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS corporate_number TEXT;           -- 法人番号
ALTER TABLE leads ADD COLUMN IF NOT EXISTS capital_stock BIGINT;            -- 資本金（円）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS annual_revenue BIGINT;          -- 売上高（円）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS operating_profit BIGINT;        -- 営業利益（円）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS sub_industry TEXT;              -- サブ業種（金属加工/機械製造等）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS prefecture TEXT;                -- 都道府県
ALTER TABLE leads ADD COLUMN IF NOT EXISTS city TEXT;                      -- 市区町村
ALTER TABLE leads ADD COLUMN IF NOT EXISTS website_url TEXT;               -- 企業サイト
ALTER TABLE leads ADD COLUMN IF NOT EXISTS establishment_year INTEGER;     -- 設立年
ALTER TABLE leads ADD COLUMN IF NOT EXISTS representative TEXT;            -- 代表者名
ALTER TABLE leads ADD COLUMN IF NOT EXISTS business_overview TEXT;         -- 事業概要

-- セグメント分類（自動計算）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS revenue_segment TEXT;           -- micro/small/mid/large/enterprise
ALTER TABLE leads ADD COLUMN IF NOT EXISTS profit_segment TEXT;            -- below_target/target_core/target_upper/out_of_range
ALTER TABLE leads ADD COLUMN IF NOT EXISTS priority_tier TEXT;             -- S/A/B/C

-- インデックス
CREATE INDEX IF NOT EXISTS idx_leads_industry_segment ON leads(company_id, industry, revenue_segment);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(company_id, priority_tier, score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_corporate_number ON leads(corporate_number);


-- === 027_learned_rules.sql ===

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


-- === 028_prompt_versions.sql ===

-- 027_prompt_versions.sql
-- プロンプトバージョン管理テーブル
-- PromptVersion dataclass のDB永続化・改善履歴トラッキング用

CREATE TABLE IF NOT EXISTS prompt_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),  -- NULLなら全社共通
    pipeline TEXT NOT NULL,
    step_name TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    prompt_text TEXT NOT NULL,
    accuracy_before NUMERIC(4,3),
    accuracy_after NUMERIC(4,3),
    change_reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- pipeline+step_name で高速ルックアップ
CREATE INDEX IF NOT EXISTS idx_prompt_versions_lookup
    ON prompt_versions(pipeline, step_name, is_active);

-- 全社共通のアクティブバージョンはpipeline+step_nameで1件のみ許可
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_active
    ON prompt_versions(pipeline, step_name)
    WHERE is_active = TRUE AND company_id IS NULL;

-- RLS有効化
ALTER TABLE prompt_versions ENABLE ROW LEVEL SECURITY;

-- サービスロールは全件アクセス可
CREATE POLICY "service_role_all" ON prompt_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 認証ユーザーは自社データのみ参照・操作可（NULLは全社共通として全員参照可）
CREATE POLICY "tenant_select" ON prompt_versions
    FOR SELECT TO authenticated
    USING (company_id IS NULL OR company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));

CREATE POLICY "tenant_insert" ON prompt_versions
    FOR INSERT TO authenticated
    WITH CHECK (company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));

CREATE POLICY "tenant_update" ON prompt_versions
    FOR UPDATE TO authenticated
    USING (company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));


-- === 029_bpo_case_studies.sql ===

-- 029: BPO導入事例DB — 営業提案用「御社と似た会社ではこういう効果が出ました」
-- bpo_case_studies: 事例基本情報
-- bpo_case_milestones: 月次マイルストーン
-- bpo_case_tags: 検索用タグ

-- =====================================================================
-- bpo_case_studies — 導入事例の基本情報
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_studies (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    industry_code       TEXT NOT NULL,
        -- 製造業: 切削 / 板金 / 射出 / 鋳造 / 食品 / 組立 / 溶接 / 塗装
        -- 建設業: 土木 / 建築 / 設備 / 電気 / 管工事
        -- 医療福祉: 歯科 / クリニック / 介護 / 訪看
        -- 不動産: 賃貸管理 / 売買仲介 / PM
        -- 物流: 倉庫 / 運送 / 3PL
        -- 卸売: 食品卸 / 機械部品卸 / 建材卸
    employee_count      INT,
    annual_revenue      BIGINT,                   -- 円単位
    challenge_category  TEXT NOT NULL,
        -- 設備保全 / 人材 / DX / 品質 / 見積 / 原価管理 / 在庫 / 安全 / 営業 / バックオフィス
    challenge_description TEXT NOT NULL,
    solution_description  TEXT NOT NULL,
    bpo_plan            TEXT NOT NULL CHECK (bpo_plan IN ('common', 'industry_specific')),
    monthly_fee         INT,                      -- 月額料金（円）
    before_monthly_hours NUMERIC(8,1),            -- 導入前: 月間作業時間
    after_monthly_hours  NUMERIC(8,1),            -- 導入後: 月間作業時間
    before_annual_cost   BIGINT,                  -- 導入前: 年間コスト（円）
    after_annual_cost    BIGINT,                  -- 導入後: 年間コスト（円）
    annual_savings       BIGINT,                  -- 年間削減額（円）
    roi_months           NUMERIC(4,1),            -- 投資回収期間（月）
    start_date           DATE,
    status               TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'completed')),
    is_public            BOOLEAN NOT NULL DEFAULT false,  -- 外部公開可の匿名事例
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_case_studies_company
    ON bpo_case_studies(company_id);
CREATE INDEX IF NOT EXISTS idx_case_studies_industry
    ON bpo_case_studies(industry_code);
CREATE INDEX IF NOT EXISTS idx_case_studies_challenge
    ON bpo_case_studies(challenge_category);

ALTER TABLE bpo_case_studies ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_studies_tenant_isolation ON bpo_case_studies
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- bpo_case_milestones — 導入の月次マイルストーン
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_milestones (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    case_id             UUID NOT NULL REFERENCES bpo_case_studies(id) ON DELETE CASCADE,
    month_number        INT NOT NULL CHECK (month_number BETWEEN 1 AND 12),
    milestone_description TEXT NOT NULL,
    actual_savings      BIGINT,                   -- その月の削減額（円）
    cumulative_savings  BIGINT,                   -- 累計削減額（円）
    satisfaction_score  INT CHECK (satisfaction_score BETWEEN 1 AND 5),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (case_id, month_number)
);

CREATE INDEX IF NOT EXISTS idx_case_milestones_case
    ON bpo_case_milestones(case_id);

ALTER TABLE bpo_case_milestones ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_milestones_tenant_isolation ON bpo_case_milestones
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- bpo_case_tags — 事例の検索用タグ
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_tags (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    case_id             UUID NOT NULL REFERENCES bpo_case_studies(id) ON DELETE CASCADE,
    tag_name            TEXT NOT NULL,
        -- 例: '5軸MC', 'IATF16949', '外国人材', 'ISO9001', 'HACCP',
        --     '多品種少量', 'JIT納品', '24h稼働', '海外取引'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (case_id, tag_name)
);

CREATE INDEX IF NOT EXISTS idx_case_tags_case
    ON bpo_case_tags(case_id);
CREATE INDEX IF NOT EXISTS idx_case_tags_name
    ON bpo_case_tags(tag_name);

ALTER TABLE bpo_case_tags ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_tags_tenant_isolation ON bpo_case_tags
    USING (company_id = current_setting('app.company_id')::uuid);

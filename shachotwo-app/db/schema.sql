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

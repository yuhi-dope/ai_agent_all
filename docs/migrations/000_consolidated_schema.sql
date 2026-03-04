-- ============================================================
-- 統合スキーマ（新規環境構築用）
-- 001〜020 の全マイグレーションを統合した最終形
-- 既存環境では個別マイグレーションを順番に適用すること
-- ============================================================

-- ============================================================
-- companies: テナント（企業）
-- 003 + 004 + 006 + 007 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    -- プロフィール (004)
    employee_count TEXT,
    annual_revenue TEXT,
    industry TEXT,
    onboarding JSONB DEFAULT '{}',
    -- 認証 (006)
    password_hash TEXT,
    -- インフラトークン (007)
    supabase_mgmt_token_enc TEXT,
    supabase_mgmt_token_expires_at TIMESTAMPTZ,
    vercel_token_enc TEXT,
    vercel_token_expires_at TIMESTAMPTZ,
    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now()
);

COMMENT ON COLUMN companies.supabase_mgmt_token_enc IS 'Fernet 暗号化された Supabase Management API トークン';
COMMENT ON COLUMN companies.vercel_token_enc IS 'Fernet 暗号化された Vercel API トークン';

CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug);

-- ============================================================
-- user_companies: ユーザー ↔ 企業マッピング
-- 003 + 008 統合（role: owner=運営者, admin=企業管理者, member=一般）
-- ============================================================
CREATE TABLE IF NOT EXISTS user_companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    company_id UUID NOT NULL REFERENCES companies(id),
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, company_id)
);

COMMENT ON COLUMN user_companies.role IS 'owner=プラットフォーム運営者, admin=企業管理者, member=一般メンバー';

CREATE INDEX IF NOT EXISTS idx_user_companies_user ON user_companies(user_id);
CREATE INDEX IF NOT EXISTS idx_user_companies_company ON user_companies(company_id);

-- ============================================================
-- runs: 開発エージェントの実行記録
-- 001 + 003(company_id) + 017(failure columns) 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL,
    company_id UUID REFERENCES companies(id),
    requirement_summary TEXT,
    spec_purpose TEXT,
    status TEXT NOT NULL,
    retry_count INT DEFAULT 0,
    output_subdir TEXT,
    genre TEXT,
    genre_override_reason TEXT,
    spec_markdown TEXT,
    notion_page_id TEXT,
    state_snapshot JSONB,
    -- 失敗追跡 (017)
    failure_reason TEXT,
    failure_category TEXT,
    error_logs JSONB DEFAULT '[]',
    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_run_id ON runs(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_genre ON runs(genre);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_company_id ON runs(company_id);
CREATE INDEX IF NOT EXISTS idx_runs_failure_category ON runs(failure_category)
    WHERE failure_category IS NOT NULL;

-- ============================================================
-- features: run ごとの機能要約
-- 001 + 003(company_id) 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS features (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL,
    company_id UUID REFERENCES companies(id),
    summary TEXT,
    file_list JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_features_run_id ON features(run_id);
CREATE INDEX IF NOT EXISTS idx_features_company_id ON features(company_id);

-- ============================================================
-- rule_changes: ルール自動マージ + レビューワークフロー
-- 001 + 003(status, reviewed_by, reviewed_at, genre) 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS rule_changes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    added_block TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    genre TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_changes_run_id ON rule_changes(run_id);
CREATE INDEX IF NOT EXISTS idx_rule_changes_status ON rule_changes(status);

-- ============================================================
-- audit_logs: ツール呼び出し監査ログ
-- 001 + 012(SaaS columns) 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments JSONB,
    result_summary JSONB,
    source TEXT NOT NULL DEFAULT 'sandbox',
    -- SaaS 監査 (012)
    company_id UUID REFERENCES companies(id),
    saas_name TEXT,
    genre TEXT,
    connection_id UUID,
    -- タイムスタンプ
    logged_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_run_id ON audit_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_logged_at ON audit_logs(logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_company_id ON audit_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_saas_name ON audit_logs(saas_name);
CREATE INDEX IF NOT EXISTS idx_audit_logs_source ON audit_logs(source);

-- ============================================================
-- oauth_tokens: OAuth トークン管理
-- 002
-- ============================================================
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_type TEXT DEFAULT 'Bearer',
    expires_at TIMESTAMPTZ,
    scopes TEXT,
    raw_response JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_tokens_provider_tenant
    ON oauth_tokens(provider, tenant_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider);

-- ============================================================
-- invite_tokens: セキュアメンバー招待
-- 005
-- ============================================================
CREATE TABLE IF NOT EXISTS invite_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    token TEXT NOT NULL UNIQUE,
    created_by TEXT,
    role TEXT NOT NULL DEFAULT 'member',
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invite_tokens_token ON invite_tokens(token);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_company ON invite_tokens(company_id);

-- ============================================================
-- channel_configs: テナントごとのチャネル設定
-- 010（company_id 型修正済み: TEXT → UUID）
-- ============================================================
CREATE TABLE IF NOT EXISTS channel_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    config_enc TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(company_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_channel_configs_company ON channel_configs(company_id);

-- ============================================================
-- company_saas_connections: SaaS 接続管理
-- 011
-- ============================================================
CREATE TABLE IF NOT EXISTS company_saas_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    saas_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    department TEXT,
    auth_method TEXT NOT NULL,
    token_secret_name TEXT,
    mcp_server_type TEXT NOT NULL,
    mcp_server_config JSONB,
    instance_url TEXT,
    scopes TEXT[],
    status TEXT NOT NULL DEFAULT 'active',
    error_message TEXT,
    connected_at TIMESTAMPTZ DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    last_health_check_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (company_id, saas_name, department)
);

CREATE INDEX IF NOT EXISTS idx_csc_company ON company_saas_connections(company_id);
CREATE INDEX IF NOT EXISTS idx_csc_saas ON company_saas_connections(saas_name);
CREATE INDEX IF NOT EXISTS idx_csc_genre ON company_saas_connections(genre);
CREATE INDEX IF NOT EXISTS idx_csc_status ON company_saas_connections(status);

ALTER TABLE company_saas_connections ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'company_saas_connections' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON company_saas_connections
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- saas_tasks: BPO タスク管理
-- 013 + 015(saas_context) + 016(plan_confidence, plan_warnings) 統合
-- ============================================================
CREATE TABLE IF NOT EXISTS saas_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    connection_id UUID REFERENCES company_saas_connections(id),
    task_id TEXT NOT NULL UNIQUE,
    task_description TEXT NOT NULL,
    saas_name TEXT NOT NULL,
    genre TEXT,
    -- 実行計画
    plan_markdown TEXT,
    planned_operations JSONB DEFAULT '[]',
    operation_count INT DEFAULT 0,
    saas_context TEXT,
    plan_confidence REAL DEFAULT 0.0,
    plan_warnings JSONB DEFAULT '[]',
    -- ステータス
    status TEXT NOT NULL DEFAULT 'planning',
    dry_run BOOLEAN DEFAULT false,
    -- 実行結果
    result_summary JSONB,
    report_markdown TEXT,
    duration_ms INT,
    -- 失敗追跡
    failure_reason TEXT,
    failure_category TEXT,
    failure_detail TEXT,
    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now(),
    approved_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_saas_tasks_company ON saas_tasks(company_id);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_status ON saas_tasks(status);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_saas_name ON saas_tasks(saas_name);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_failure ON saas_tasks(failure_category)
    WHERE failure_category IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_saas_tasks_created ON saas_tasks(created_at DESC);

ALTER TABLE saas_tasks ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'saas_tasks' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON saas_tasks
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- operation_patterns: 操作パターン検出
-- 014
-- ============================================================
CREATE TABLE IF NOT EXISTS operation_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    pattern_type TEXT NOT NULL,
    saas_name TEXT,
    genre TEXT,
    pattern_key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    pattern_data JSONB NOT NULL DEFAULT '{}',
    occurrence_count INT NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'detected',
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_op_patterns_company ON operation_patterns(company_id);
CREATE INDEX IF NOT EXISTS idx_op_patterns_type ON operation_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_op_patterns_key ON operation_patterns(pattern_key);
CREATE INDEX IF NOT EXISTS idx_op_patterns_status ON operation_patterns(status);

ALTER TABLE operation_patterns ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'operation_patterns' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON operation_patterns
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- saas_schema_snapshots: SaaS スキーマスナップショット
-- 014
-- ============================================================
CREATE TABLE IF NOT EXISTS saas_schema_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    connection_id UUID,
    saas_name TEXT NOT NULL,
    schema_data JSONB NOT NULL DEFAULT '{}',
    snapshot_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schema_snap_company ON saas_schema_snapshots(company_id);
CREATE INDEX IF NOT EXISTS idx_schema_snap_saas ON saas_schema_snapshots(saas_name);
CREATE INDEX IF NOT EXISTS idx_schema_snap_created ON saas_schema_snapshots(created_at DESC);

ALTER TABLE saas_schema_snapshots ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'saas_schema_snapshots' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON saas_schema_snapshots
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- saas_structure_knowledge: SaaS 構造ナレッジ自動蓄積
-- 018（company_id 型修正済み: TEXT → UUID）
-- ============================================================
CREATE TABLE IF NOT EXISTS saas_structure_knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    saas_name TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    structure_type TEXT NOT NULL,
    structure_data JSONB NOT NULL,
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
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- bpo_specialist_maturity: BPO 専門化成熟度スコア
-- 019（company_id 型修正済み: TEXT → UUID, NULL許容）
-- ============================================================
CREATE TABLE IF NOT EXISTS bpo_specialist_maturity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id) ON DELETE CASCADE,
    saas_name TEXT NOT NULL,
    genre TEXT NOT NULL,
    score NUMERIC(4,3) NOT NULL DEFAULT 0,
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
                OR company_id = current_setting('app.company_id', true)::UUID
            );
    END IF;
END $$;

-- ============================================================
-- task_corrections: タスク修正履歴（修正駆動学習用）
-- 020
-- ============================================================
CREATE TABLE IF NOT EXISTS task_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    saas_name TEXT NOT NULL,
    genre TEXT,
    -- 修正前スナップショット
    original_description TEXT NOT NULL,
    original_plan_markdown TEXT,
    original_planned_operations JSONB DEFAULT '[]',
    original_confidence NUMERIC(4,3),
    original_warnings JSONB DEFAULT '[]',
    original_status TEXT,
    original_failure_reason TEXT,
    original_failure_category TEXT,
    -- 修正後
    modified_description TEXT NOT NULL,
    -- メタ
    correction_type TEXT DEFAULT 'description_change',
    user_notes TEXT,
    outcome TEXT DEFAULT 'pending',
    outcome_updated_at TIMESTAMPTZ,
    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_corrections_task ON task_corrections(task_id);
CREATE INDEX IF NOT EXISTS idx_task_corrections_saas ON task_corrections(saas_name);
CREATE INDEX IF NOT EXISTS idx_task_corrections_outcome ON task_corrections(outcome);
CREATE INDEX IF NOT EXISTS idx_task_corrections_created ON task_corrections(created_at DESC);

ALTER TABLE task_corrections ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'task_corrections' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON task_corrections
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

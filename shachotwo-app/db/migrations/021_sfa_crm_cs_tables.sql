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

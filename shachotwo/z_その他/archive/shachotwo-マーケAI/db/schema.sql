-- =============================================================
-- shachotwo-マーケAI  DB Schema
-- =============================================================

-- 営業対象企業
CREATE TABLE apo_companies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    industry        TEXT NOT NULL,
    website_url     TEXT,
    employee_count  INTEGER,
    prefecture      TEXT,
    city            TEXT,
    corporate_number TEXT,
    capital         BIGINT,
    representative  TEXT,
    establishment_year INTEGER,
    research_data   JSONB DEFAULT '{}',
    pain_points     TEXT[],
    lp_url          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- リード（「話を聞きたい」を押した人）
CREATE TABLE apo_leads (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID REFERENCES apo_companies(id),
    contact_name    TEXT NOT NULL,
    phone           TEXT NOT NULL,
    email           TEXT,
    source          TEXT NOT NULL DEFAULT 'lp_cta',
    temperature     TEXT NOT NULL DEFAULT 'hot',
    status          TEXT NOT NULL DEFAULT 'new',
    lost_reason     TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- アウトリーチ履歴
CREATE TABLE apo_outreach_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID REFERENCES apo_companies(id),
    variant_id      UUID,
    channel         TEXT NOT NULL,
    action          TEXT NOT NULL,
    subject         TEXT,
    body_preview    TEXT,
    sent_at         TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ,
    clicked_at      TIMESTAMPTZ,
    replied_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- LP閲覧ログ
CREATE TABLE apo_page_views (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID REFERENCES apo_companies(id),
    page_url        TEXT NOT NULL,
    duration_sec    INTEGER,
    cta_clicked     BOOLEAN DEFAULT FALSE,
    doc_downloaded  BOOLEAN DEFAULT FALSE,
    ip_address      TEXT,
    user_agent      TEXT,
    viewed_at       TIMESTAMPTZ DEFAULT now()
);

-- 商談
CREATE TABLE apo_deals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_id         UUID REFERENCES apo_leads(id),
    company_id      UUID REFERENCES apo_companies(id),
    stage           TEXT NOT NULL DEFAULT 'appointment',
    meeting_date    TIMESTAMPTZ,
    meeting_url     TEXT,
    meeting_notes   TEXT,
    proposed_plan   TEXT,
    monthly_amount  INTEGER,
    close_date      DATE,
    closed_at       TIMESTAMPTZ,
    lost_reason     TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- A/Bテスト実験
CREATE TABLE apo_experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    test_element    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    start_date      DATE NOT NULL,
    end_date        DATE,
    winner_variant  TEXT,
    analysis        TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- バリアント
CREATE TABLE apo_variants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   UUID REFERENCES apo_experiments(id),
    name            TEXT NOT NULL,
    content         JSONB NOT NULL,
    send_count      INTEGER DEFAULT 0,
    open_count      INTEGER DEFAULT 0,
    click_count     INTEGER DEFAULT 0,
    lead_count      INTEGER DEFAULT 0,
    appointment_count INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 配信停止
CREATE TABLE apo_unsubscribes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID REFERENCES apo_companies(id),
    email           TEXT,
    reason          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

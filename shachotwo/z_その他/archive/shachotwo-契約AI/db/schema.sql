-- =============================================================
-- shachotwo-契約AI  DB Schema
-- =============================================================

-- 契約
CREATE TABLE apo_contracts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id                 UUID,
    company_id              UUID,
    lead_id                 UUID,

    -- 契約情報
    plan                    TEXT NOT NULL,
    monthly_amount          INTEGER NOT NULL,
    billing_cycle           TEXT NOT NULL DEFAULT 'monthly',
    trial_days              INTEGER DEFAULT 30,

    -- 電子契約
    cloudsign_document_id   TEXT,
    contract_pdf_url        TEXT,
    signed_at               TIMESTAMPTZ,

    -- 見積書
    estimate_number         TEXT NOT NULL,
    estimate_pdf_url        TEXT,
    estimate_valid_until    DATE,

    -- 決済
    stripe_customer_id      TEXT,
    stripe_subscription_id  TEXT,
    payment_method          TEXT NOT NULL DEFAULT 'credit_card',
    first_payment_at        TIMESTAMPTZ,

    -- ステータス
    status                  TEXT NOT NULL DEFAULT 'estimate_sent',
    cancelled_at            TIMESTAMPTZ,
    cancel_reason           TEXT,

    -- オンボーディング
    account_created_at      TIMESTAMPTZ,
    onboarding_call_at      TIMESTAMPTZ,
    onboarding_status       TEXT DEFAULT 'pending',

    created_at              TIMESTAMPTZ DEFAULT now(),
    updated_at              TIMESTAMPTZ DEFAULT now()
);

-- 契約ステータス履歴
CREATE TABLE apo_contract_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id     UUID REFERENCES apo_contracts(id),
    event_type      TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 失注分析
CREATE TABLE apo_lost_reasons (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deal_id         UUID,
    company_id      UUID,
    reason_category TEXT NOT NULL,
    reason_detail   TEXT,
    competitor_name TEXT,
    retry_date      DATE,
    created_at      TIMESTAMPTZ DEFAULT now()
);

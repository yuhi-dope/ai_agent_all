-- ============================================================
-- 044_partner_apps.sql
-- Partner Marketplace: パートナー・アプリ・インストール・レビュー
-- ============================================================

-- ─────────────────────────────────────────────────────────
-- パートナー企業テーブル
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partners (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    display_name        TEXT        NOT NULL,
    partner_type        TEXT        NOT NULL DEFAULT 'sharoushi',
        -- sharoushi | zeirishi | gyoseishoshi | bengoshi | other
    contact_email       TEXT,
    stripe_account_id   TEXT,           -- Stripe Connect（支払い受取用）
    revenue_share_rate  NUMERIC(4,3)    DEFAULT 0.700,  -- パートナー取り分（デフォルト70%）
    is_approved         BOOLEAN         DEFAULT false,
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ     DEFAULT now()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_partners_company_id ON partners(company_id);
CREATE INDEX IF NOT EXISTS idx_partners_is_approved ON partners(is_approved);

-- RLS 有効化
ALTER TABLE partners ENABLE ROW LEVEL SECURITY;

-- SELECT: 全社参照可（公開情報）
CREATE POLICY "partners_select_all" ON partners
    FOR SELECT USING (true);

-- INSERT: 自社のみ登録可
CREATE POLICY "partners_insert_own" ON partners
    FOR INSERT WITH CHECK (company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid);

-- UPDATE: 自社 or システム管理（service_role）のみ更新可
CREATE POLICY "partners_update_own" ON partners
    FOR UPDATE USING (
        company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
    );


-- ─────────────────────────────────────────────────────────
-- アプリテーブル
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS partner_apps (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id      UUID        NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    name            TEXT        NOT NULL,
    description     TEXT,
    category        TEXT        NOT NULL,       -- bpo | template | connector
    price_yen       INTEGER     NOT NULL DEFAULT 0,
    pricing_model   TEXT        DEFAULT 'monthly',  -- monthly | one_time | free
    genome_config   JSONB,                      -- カスタムゲノムJSON
    pipeline_config JSONB,                      -- パイプライン設定
    icon_url        TEXT,
    status          TEXT        DEFAULT 'draft',
        -- draft | review | published | unpublished
    install_count   INTEGER     DEFAULT 0,
    rating_avg      NUMERIC(3,2),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_partner_apps_partner_id  ON partner_apps(partner_id);
CREATE INDEX IF NOT EXISTS idx_partner_apps_status      ON partner_apps(status);
CREATE INDEX IF NOT EXISTS idx_partner_apps_category    ON partner_apps(category);

-- RLS 有効化
ALTER TABLE partner_apps ENABLE ROW LEVEL SECURITY;

-- SELECT: published のみ全社参照可
CREATE POLICY "partner_apps_select_published" ON partner_apps
    FOR SELECT USING (
        status = 'published'
        OR partner_id IN (
            SELECT id FROM partners
            WHERE company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
        )
    );

-- INSERT: パートナー自身（自社のパートナーレコード経由）のみ
CREATE POLICY "partner_apps_insert_own" ON partner_apps
    FOR INSERT WITH CHECK (
        partner_id IN (
            SELECT id FROM partners
            WHERE company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
        )
    );

-- UPDATE: パートナー自身のみ
CREATE POLICY "partner_apps_update_own" ON partner_apps
    FOR UPDATE USING (
        partner_id IN (
            SELECT id FROM partners
            WHERE company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
        )
    );


-- ─────────────────────────────────────────────────────────
-- インストールテーブル
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_installations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id          UUID        NOT NULL REFERENCES partner_apps(id) ON DELETE CASCADE,
    company_id      UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    installed_at    TIMESTAMPTZ DEFAULT now(),
    config          JSONB,          -- 会社ごとのカスタム設定
    is_active       BOOLEAN     DEFAULT true,
    UNIQUE(app_id, company_id)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_app_installations_company_id ON app_installations(company_id);
CREATE INDEX IF NOT EXISTS idx_app_installations_app_id     ON app_installations(app_id);

-- RLS 有効化
ALTER TABLE app_installations ENABLE ROW LEVEL SECURITY;

-- SELECT/INSERT/UPDATE/DELETE: company_id ベース
CREATE POLICY "app_installations_own_company" ON app_installations
    FOR ALL USING (
        company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
    );


-- ─────────────────────────────────────────────────────────
-- レビューテーブル
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_reviews (
    id                  UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id              UUID    NOT NULL REFERENCES partner_apps(id) ON DELETE CASCADE,
    company_id          UUID    NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    reviewer_user_id    UUID,
    rating              INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment             TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(app_id, company_id)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_app_reviews_app_id     ON app_reviews(app_id);
CREATE INDEX IF NOT EXISTS idx_app_reviews_company_id ON app_reviews(company_id);

-- RLS 有効化
ALTER TABLE app_reviews ENABLE ROW LEVEL SECURITY;

-- SELECT: 全社参照可
CREATE POLICY "app_reviews_select_all" ON app_reviews
    FOR SELECT USING (true);

-- INSERT/UPDATE: company_id 本人のみ
CREATE POLICY "app_reviews_insert_own" ON app_reviews
    FOR INSERT WITH CHECK (
        company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
    );

CREATE POLICY "app_reviews_update_own" ON app_reviews
    FOR UPDATE USING (
        company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
    );


-- ─────────────────────────────────────────────────────────
-- updated_at 自動更新トリガー
-- ─────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_partner_apps_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_partner_apps_updated_at ON partner_apps;
CREATE TRIGGER trg_partner_apps_updated_at
    BEFORE UPDATE ON partner_apps
    FOR EACH ROW EXECUTE FUNCTION update_partner_apps_updated_at();

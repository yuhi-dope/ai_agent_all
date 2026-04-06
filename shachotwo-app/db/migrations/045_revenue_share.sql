-- ============================================================
-- 045_revenue_share.sql
-- Partner Marketplace: 収益分配テーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS revenue_share_records (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_id          UUID        NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    app_id              UUID        NOT NULL REFERENCES partner_apps(id) ON DELETE CASCADE,
    company_id          UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,  -- 購入した会社
    period_month        TEXT        NOT NULL,           -- YYYY-MM
    gross_amount_yen    INTEGER     NOT NULL,           -- 総売上
    partner_amount_yen  INTEGER     NOT NULL,           -- パートナー取り分
    platform_amount_yen INTEGER     NOT NULL,           -- シャチョツー取り分
    revenue_share_rate  NUMERIC(4,3),
    stripe_payout_id    TEXT,                          -- Stripe送金ID
    status              TEXT        DEFAULT 'pending', -- pending | paid | failed
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_revenue_share_partner_period
    ON revenue_share_records(partner_id, period_month);

CREATE INDEX IF NOT EXISTS idx_revenue_share_status
    ON revenue_share_records(status);

CREATE INDEX IF NOT EXISTS idx_revenue_share_app_id
    ON revenue_share_records(app_id);

-- RLS 有効化
ALTER TABLE revenue_share_records ENABLE ROW LEVEL SECURITY;

-- SELECT: partner_id に紐づく会社のみ参照可
CREATE POLICY "revenue_share_select_partner_own" ON revenue_share_records
    FOR SELECT USING (
        partner_id IN (
            SELECT id FROM partners
            WHERE company_id = (current_setting('request.jwt.claims', true)::jsonb->>'company_id')::uuid
        )
    );

-- INSERT/UPDATE: service_role のみ（バックエンドサーバーサイドで管理）
-- ※ RLS の INSERT ポリシーを設けず、service_role による direct insert のみ許可

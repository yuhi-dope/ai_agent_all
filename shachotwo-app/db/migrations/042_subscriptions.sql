-- ============================================================
-- 042_subscriptions.sql
-- スケール課金モデル（REQ-3003）— サブスクリプション管理テーブル
-- ============================================================

-- subscriptions テーブル
CREATE TABLE IF NOT EXISTS subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id              UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    -- プラン
    -- common_bpo          : 共通BPO ¥150,000/月
    -- industry_bpo        : 業種特化BPO ¥300,000/月
    -- industry_bpo_support: 業種特化BPO + 人間サポート ¥450,000/月
    plan                    TEXT NOT NULL CHECK (plan IN ('common_bpo', 'industry_bpo', 'industry_bpo_support')),

    -- ステータス
    -- active    : 課金中（有効）
    -- canceled  : 解約済み（期末まで有効）
    -- past_due  : 支払い失敗
    -- trialing  : 無料トライアル中
    status                  TEXT NOT NULL DEFAULT 'trialing'
                                CHECK (status IN ('active', 'canceled', 'past_due', 'trialing')),

    -- Stripe 連携
    stripe_subscription_id  TEXT UNIQUE,
    stripe_customer_id      TEXT,

    -- 課金期間
    current_period_start    TIMESTAMPTZ,
    current_period_end      TIMESTAMPTZ,
    cancel_at_period_end    BOOLEAN NOT NULL DEFAULT false,

    -- 業種（NULL = common_bpo）
    -- 例: 'construction' | 'manufacturing' | 'medical' | 'real_estate' | 'logistics' | 'wholesale'
    industry                TEXT,

    -- オンボーディング種別
    -- self          : セルフ（無料）
    -- consulting    : コンサル型（¥50,000 × 2ヶ月）
    -- full_support  : フルサポート（¥300,000 × 3ヶ月）
    onboarding_type         TEXT NOT NULL DEFAULT 'self'
                                CHECK (onboarding_type IN ('self', 'consulting', 'full_support')),
    onboarding_fee_yen      INTEGER NOT NULL DEFAULT 0,

    -- タイムスタンプ
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_subscriptions_company_id ON subscriptions (company_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer_id ON subscriptions (stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions (status);

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_subscriptions_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_subscriptions_updated_at
BEFORE UPDATE ON subscriptions
FOR EACH ROW EXECUTE FUNCTION update_subscriptions_updated_at();

-- ============================================================
-- RLS（Row Level Security）
-- 全テーブル company_id ベースの RLS 必須
-- ============================================================
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

-- admin のみ自社のサブスクリプションを参照できる
CREATE POLICY subscriptions_select_admin ON subscriptions
    FOR SELECT
    USING (
        company_id = (auth.jwt() -> 'app_metadata' ->> 'company_id')::UUID
        AND (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
    );

-- admin のみ自社のサブスクリプションを挿入できる
CREATE POLICY subscriptions_insert_admin ON subscriptions
    FOR INSERT
    WITH CHECK (
        company_id = (auth.jwt() -> 'app_metadata' ->> 'company_id')::UUID
        AND (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
    );

-- admin のみ自社のサブスクリプションを更新できる
CREATE POLICY subscriptions_update_admin ON subscriptions
    FOR UPDATE
    USING (
        company_id = (auth.jwt() -> 'app_metadata' ->> 'company_id')::UUID
        AND (auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'
    );

-- サービスロール（バックエンド）は全操作可能
-- ※ Supabase service_role キー利用時は RLS を bypass するため追加ポリシー不要

COMMENT ON TABLE subscriptions IS 'サブスクリプション管理テーブル（REQ-3003）。plan/statusの変更はStripe Webhookで自動更新される。';

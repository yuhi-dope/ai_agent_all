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

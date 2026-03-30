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

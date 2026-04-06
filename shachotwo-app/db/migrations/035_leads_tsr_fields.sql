-- leads: TSR情報フィールド追加（大分類/中分類/小分類/細分類/営業種目/仕入先/販売先/代表者/代表番号/直近売上/直近純利益）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_category_large   TEXT;  -- TSR大分類（製造業/建設業等）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_category_medium  TEXT;  -- TSR中分類（輸送用機械器具製造業等）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_category_small   TEXT;  -- TSR小分類（自動車・同附属品製造業等）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_category_detail  TEXT;  -- TSR細分類
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_business_items   TEXT;  -- 営業種目（自動車製造業(100%)等）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_suppliers         TEXT;  -- 仕入先
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_customers         TEXT;  -- 販売先
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_representative   TEXT;  -- 代表者（TSR）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS representative_phone TEXT;  -- 代表番号
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_revenue_latest   BIGINT; -- 直近期売上（千円）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tsr_profit_latest    BIGINT; -- 直近期純利益（千円）

-- TSR分類での検索用インデックス
CREATE INDEX IF NOT EXISTS idx_leads_tsr_category ON leads(tsr_category_large, tsr_category_medium);

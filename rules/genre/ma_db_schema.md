# M&A DBスキーマテンプレート

```sql
-- ターゲット企業
CREATE TABLE IF NOT EXISTS ma_targets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  industry TEXT,
  revenue BIGINT,                 -- 年商（円）
  employees INT,
  location TEXT,
  source TEXT,                    -- 紹介, 自社リサーチ, アドバイザー, データベース
  status TEXT DEFAULT 'ロングリスト',  -- ロングリスト, ショートリスト, 初期接触, NDA締結, DD実施, 条件交渉, 最終合意, クロージング, 見送り
  shortlist_reason TEXT,
  risk_score INT DEFAULT 0,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 企業価値評価
CREATE TABLE IF NOT EXISTS ma_valuations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  target_id UUID NOT NULL REFERENCES ma_targets(id),
  method TEXT NOT NULL,           -- DCF, マルチプル, 純資産
  estimated_value BIGINT NOT NULL,
  assumptions JSONB DEFAULT '{}', -- 手法ごとの前提条件
  evaluated_by TEXT,
  evaluated_at TIMESTAMPTZ DEFAULT now()
);

-- DDチェックリスト
CREATE TABLE IF NOT EXISTS ma_dd_checklist (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  target_id UUID NOT NULL REFERENCES ma_targets(id),
  category TEXT NOT NULL,         -- 財務, 法務, 税務, 人事, IT
  item TEXT NOT NULL,
  status TEXT DEFAULT '未着手',   -- 未着手, 調査中, 完了, 問題あり, 該当なし
  findings TEXT,
  risk_level TEXT,                -- 高, 中, 低（問題ありの場合）
  assigned_to TEXT,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- M&A案件
CREATE TABLE IF NOT EXISTS ma_deals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  target_id UUID NOT NULL REFERENCES ma_targets(id),
  deal_type TEXT NOT NULL,        -- 買収, 合併, 資本提携, 事業譲渡
  stage TEXT DEFAULT '検討中',
  offer_amount BIGINT,
  advisor TEXT,
  expected_close_date DATE,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 競合分析
CREATE TABLE IF NOT EXISTS ma_competitor_analysis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  target_id UUID NOT NULL REFERENCES ma_targets(id),
  competitor_name TEXT NOT NULL,
  metric TEXT NOT NULL,           -- 売上, 利益率, 市場シェア, 従業員数 等
  target_value TEXT,
  competitor_value TEXT,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

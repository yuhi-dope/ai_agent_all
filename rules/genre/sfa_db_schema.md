# SFA DBスキーマテンプレート

```sql
-- ステージマスタ
CREATE TABLE IF NOT EXISTS sfa_stages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,            -- リード獲得, アポ取得, ヒアリング, 提案, 見積提出, 交渉, 受注, 失注
  "order" INT NOT NULL,
  probability_default INT DEFAULT 0,  -- 受注確度(%)
  is_won BOOLEAN DEFAULT false,
  is_lost BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 商談
CREATE TABLE IF NOT EXISTS sfa_deals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  customer_name TEXT,
  amount BIGINT DEFAULT 0,       -- 円単位
  stage_id UUID REFERENCES sfa_stages(id),
  probability INT DEFAULT 0,
  expected_close_date DATE,
  assigned_to TEXT,
  lost_reason TEXT,
  closed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 商談履歴（ステージ遷移ログ）
CREATE TABLE IF NOT EXISTS sfa_deal_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  deal_id UUID NOT NULL REFERENCES sfa_deals(id),
  from_stage_id UUID REFERENCES sfa_stages(id),
  to_stage_id UUID REFERENCES sfa_stages(id),
  changed_by TEXT,
  changed_at TIMESTAMPTZ DEFAULT now()
);

-- 営業活動
CREATE TABLE IF NOT EXISTS sfa_activities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  deal_id UUID REFERENCES sfa_deals(id),
  activity_type TEXT NOT NULL,   -- 訪問, 架電, メール, Web会議, その他
  note TEXT,
  activity_date TIMESTAMPTZ DEFAULT now(),
  created_by TEXT
);

-- 見積書
CREATE TABLE IF NOT EXISTS sfa_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  deal_id UUID NOT NULL REFERENCES sfa_deals(id),
  quote_number TEXT NOT NULL,    -- INV-2026-0001 形式
  items JSONB NOT NULL DEFAULT '[]',  -- [{name, quantity, unit_price, subtotal}]
  subtotal BIGINT DEFAULT 0,
  tax BIGINT DEFAULT 0,
  total BIGINT DEFAULT 0,
  valid_until DATE,
  status TEXT DEFAULT '下書き',   -- 下書き, 送付済, 承諾, 却下
  created_at TIMESTAMPTZ DEFAULT now()
);
```

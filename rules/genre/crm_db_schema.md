# CRM DBスキーマテンプレート

```sql
-- 顧客
CREATE TABLE IF NOT EXISTS crm_customers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  segment TEXT DEFAULT 'D',      -- A/B/C/D
  status TEXT DEFAULT '新規',     -- 見込み, 新規, アクティブ, 休眠, 解約
  assigned_to TEXT,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 顧客担当者
CREATE TABLE IF NOT EXISTS crm_contacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  customer_id UUID NOT NULL REFERENCES crm_customers(id),
  name TEXT NOT NULL,
  role TEXT,
  email TEXT,
  phone TEXT,
  is_primary BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 対応履歴
CREATE TABLE IF NOT EXISTS crm_interactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  customer_id UUID NOT NULL REFERENCES crm_customers(id),
  interaction_type TEXT NOT NULL,  -- 電話, メール, 訪問, チャット, その他
  summary TEXT,
  tags TEXT[] DEFAULT '{}',       -- クレーム, 契約更新, アップセル 等
  interaction_date TIMESTAMPTZ DEFAULT now(),
  created_by TEXT
);

-- サポートチケット
CREATE TABLE IF NOT EXISTS crm_tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  customer_id UUID NOT NULL REFERENCES crm_customers(id),
  subject TEXT NOT NULL,
  description TEXT,
  priority TEXT DEFAULT 'P3',     -- P1, P2, P3, P4
  status TEXT DEFAULT '新規',     -- 新規, 対応中, 保留, 解決済, クローズ
  assigned_to TEXT,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 顧客セグメントルール
CREATE TABLE IF NOT EXISTS crm_segment_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,            -- A, B, C, D
  min_revenue BIGINT DEFAULT 0,  -- 年間取引額下限
  max_revenue BIGINT,
  follow_up_interval_days INT DEFAULT 180,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

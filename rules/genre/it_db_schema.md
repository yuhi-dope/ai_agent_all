# 情シス DBスキーマテンプレート

```sql
-- IT資産
CREATE TABLE IF NOT EXISTS it_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  asset_type TEXT NOT NULL,        -- PC, サーバー, ネットワーク機器, 周辺機器, その他
  serial_number TEXT,
  asset_code TEXT,                 -- QRコード/バーコード用
  assigned_to TEXT,
  purchase_date DATE,
  warranty_until DATE,
  status TEXT DEFAULT '在庫',      -- 在庫, 利用中, 修理中, 廃棄予定, 廃棄済
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ライセンス
CREATE TABLE IF NOT EXISTS it_licenses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  software_name TEXT NOT NULL,
  license_key TEXT,
  seats_total INT NOT NULL DEFAULT 1,
  seats_used INT NOT NULL DEFAULT 0,
  expiry_date DATE,
  vendor TEXT,
  cost_per_seat BIGINT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ヘルプデスクチケット
CREATE TABLE IF NOT EXISTS it_help_tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  reporter TEXT NOT NULL,
  subject TEXT NOT NULL,
  description TEXT,
  category TEXT DEFAULT 'その他',  -- ネットワーク, PC不具合, ソフトウェア, アカウント, その他
  priority TEXT DEFAULT 'P3',      -- P1, P2, P3, P4
  status TEXT DEFAULT '新規',      -- 新規, 対応中, 保留, 解決済, クローズ
  assigned_to TEXT,
  sla_deadline TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- アカウント管理
CREATE TABLE IF NOT EXISTS it_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  employee_id TEXT NOT NULL,
  service_name TEXT NOT NULL,
  username TEXT,
  status TEXT DEFAULT '有効',      -- 有効, 無効, 一時停止
  provisioned_at TIMESTAMPTZ DEFAULT now(),
  deactivated_at TIMESTAMPTZ
);

-- インシデント
CREATE TABLE IF NOT EXISTS it_incidents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  severity TEXT NOT NULL,          -- P1, P2, P3, P4
  affected_systems TEXT[] DEFAULT '{}',
  description TEXT,
  root_cause TEXT,
  resolution TEXT,
  prevention TEXT,                 -- 再発防止策
  started_at TIMESTAMPTZ NOT NULL,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

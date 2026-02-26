# 会計 DBスキーマテンプレート

```sql
-- 勘定科目マスタ
CREATE TABLE IF NOT EXISTS accounting_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  code TEXT NOT NULL,             -- 100, 200, 511 等
  name TEXT NOT NULL,             -- 現金, 売掛金, 旅費交通費 等
  account_type TEXT NOT NULL,     -- 資産, 負債, 純資産, 収益, 費用
  parent_id UUID REFERENCES accounting_accounts(id),
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, code)
);

-- 仕訳伝票
CREATE TABLE IF NOT EXISTS accounting_journals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  journal_number TEXT,            -- 自動採番
  journal_date DATE NOT NULL,
  description TEXT,
  status TEXT DEFAULT '下書き',    -- 下書き, 確定, 取消
  created_by TEXT,
  confirmed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 仕訳明細（借方/貸方）
CREATE TABLE IF NOT EXISTS accounting_journal_lines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  journal_id UUID NOT NULL REFERENCES accounting_journals(id) ON DELETE CASCADE,
  account_id UUID NOT NULL REFERENCES accounting_accounts(id),
  debit_amount BIGINT DEFAULT 0,   -- 円単位
  credit_amount BIGINT DEFAULT 0,
  note TEXT,
  CONSTRAINT check_debit_or_credit CHECK (
    (debit_amount > 0 AND credit_amount = 0) OR
    (debit_amount = 0 AND credit_amount > 0)
  )
);

-- 請求書
CREATE TABLE IF NOT EXISTS accounting_invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  invoice_number TEXT NOT NULL,    -- INV-2026-0001
  customer_name TEXT NOT NULL,
  issue_date DATE NOT NULL,
  due_date DATE NOT NULL,
  items JSONB NOT NULL DEFAULT '[]',  -- [{name, quantity, unit_price, tax_rate, subtotal}]
  subtotal BIGINT DEFAULT 0,
  tax BIGINT DEFAULT 0,
  total BIGINT DEFAULT 0,
  status TEXT DEFAULT '下書き',    -- 下書き, 発行済, 入金済, 督促中, 取消
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 経費精算
CREATE TABLE IF NOT EXISTS accounting_expenses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  applicant TEXT NOT NULL,
  category TEXT NOT NULL,          -- 交通費, 交際費, 消耗品費 等
  amount BIGINT NOT NULL,
  description TEXT,
  receipt_url TEXT,
  expense_date DATE NOT NULL,
  status TEXT DEFAULT '申請中',    -- 申請中, 上長承認, 経理確認, 却下
  approved_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

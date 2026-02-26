# 法務 DBスキーマテンプレート

```sql
-- 契約書
CREATE TABLE IF NOT EXISTS legal_contracts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  counterparty TEXT NOT NULL,
  contract_type TEXT NOT NULL,     -- NDA, 業務委託, 売買, ライセンス, 雇用, その他
  start_date DATE,
  end_date DATE,
  auto_renew BOOLEAN DEFAULT false,
  version INT DEFAULT 1,
  pdf_url TEXT,
  status TEXT DEFAULT '草案',      -- 草案, 承認中, 締結済, 有効, 期限切れ, 解約
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 契約条項
CREATE TABLE IF NOT EXISTS legal_clauses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  contract_id UUID NOT NULL REFERENCES legal_contracts(id) ON DELETE CASCADE,
  clause_number INT NOT NULL,
  title TEXT NOT NULL,
  content TEXT,
  risk_level TEXT DEFAULT '低',    -- 高, 中, 低
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 承認フロー（稟議）
CREATE TABLE IF NOT EXISTS legal_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  contract_id UUID NOT NULL REFERENCES legal_contracts(id),
  requester TEXT NOT NULL,
  current_step INT DEFAULT 1,
  total_steps INT NOT NULL,
  status TEXT DEFAULT '申請中',    -- 申請中, 承認済, 却下, 差戻し
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 承認ステップ
CREATE TABLE IF NOT EXISTS legal_approval_steps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  approval_id UUID NOT NULL REFERENCES legal_approvals(id) ON DELETE CASCADE,
  step_number INT NOT NULL,
  approver TEXT NOT NULL,
  action TEXT,                     -- 承認, 却下, 差戻し
  comment TEXT,
  decided_at TIMESTAMPTZ
);

-- 承認ルートマスタ
CREATE TABLE IF NOT EXISTS legal_approval_routes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  min_amount BIGINT DEFAULT 0,
  max_amount BIGINT,
  approvers JSONB NOT NULL DEFAULT '[]',  -- [{role, name}]
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 期限リマインダー
CREATE TABLE IF NOT EXISTS legal_reminders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  contract_id UUID NOT NULL REFERENCES legal_contracts(id),
  remind_date DATE NOT NULL,
  reminder_type TEXT DEFAULT '更新',  -- 更新, 解約, その他
  notified BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

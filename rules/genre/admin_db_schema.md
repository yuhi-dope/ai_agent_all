# 事務 DBスキーマテンプレート

```sql
-- 日報
CREATE TABLE IF NOT EXISTS admin_daily_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  author TEXT NOT NULL,
  report_date DATE NOT NULL,
  tasks JSONB NOT NULL DEFAULT '[]',  -- [{content, hours, category}]
  hours_worked NUMERIC(4,1) NOT NULL,
  summary TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, author, report_date)
);

-- 勤怠
CREATE TABLE IF NOT EXISTS admin_attendances (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  employee_id TEXT NOT NULL,
  date DATE NOT NULL,
  clock_in TIMESTAMPTZ,
  clock_out TIMESTAMPTZ,
  break_minutes INT DEFAULT 60,
  status TEXT DEFAULT '出勤',      -- 出勤, 有給, 半休(午前), 半休(午後), 欠勤, 遅刻
  note TEXT,
  UNIQUE(company_id, employee_id, date)
);

-- 休暇申請
CREATE TABLE IF NOT EXISTS admin_leave_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  employee_id TEXT NOT NULL,
  leave_type TEXT NOT NULL,        -- 有給休暇, 特別休暇, 半休(午前), 半休(午後)
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  days_count NUMERIC(3,1) NOT NULL,
  reason TEXT,
  status TEXT DEFAULT '申請中',    -- 申請中, 承認, 却下
  approved_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 有給残日数
CREATE TABLE IF NOT EXISTS admin_leave_balances (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  employee_id TEXT NOT NULL,
  fiscal_year INT NOT NULL,        -- 年度（4月始まり: 2026 = 2026年4月〜2027年3月）
  total_days NUMERIC(4,1) DEFAULT 20,
  used_days NUMERIC(4,1) DEFAULT 0,
  UNIQUE(company_id, employee_id, fiscal_year)
);

-- 備品管理
CREATE TABLE IF NOT EXISTS admin_equipment (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT,                   -- PC, モニター, 携帯, 什器, その他
  serial_number TEXT,
  location TEXT,
  assigned_to TEXT,
  status TEXT DEFAULT '利用可能',  -- 利用可能, 貸出中, 修理中, 廃棄予定, 廃棄済
  purchase_date DATE,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 議事録
CREATE TABLE IF NOT EXISTS admin_meeting_minutes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  meeting_date TIMESTAMPTZ NOT NULL,
  attendees TEXT[] DEFAULT '{}',
  agenda TEXT,
  decisions TEXT,
  action_items JSONB DEFAULT '[]',  -- [{assignee, task, due_date, done}]
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

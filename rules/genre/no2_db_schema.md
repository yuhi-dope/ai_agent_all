# No.2 / 経営エージェント DBスキーマテンプレート

```sql
-- KPI定義
CREATE TABLE IF NOT EXISTS no2_kpis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  name TEXT NOT NULL,             -- 月次売上, 営業利益率, 顧客数, 解約率 等
  category TEXT NOT NULL,         -- 売上, 利益, 顧客, 業務効率, 人事
  unit TEXT,                      -- 円, %, 件, 人 等
  target_value NUMERIC,
  current_value NUMERIC,
  period_type TEXT DEFAULT '月次', -- 月次, 四半期, 年次
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- KPI実績（時系列）
CREATE TABLE IF NOT EXISTS no2_kpi_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  kpi_id UUID NOT NULL REFERENCES no2_kpis(id),
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  actual_value NUMERIC NOT NULL,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, kpi_id, period_start)
);

-- 経営提言
CREATE TABLE IF NOT EXISTS no2_insights (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT,                  -- コスト削減, 売上拡大, 組織強化, プロダクト, 戦略
  summary TEXT NOT NULL,
  detail TEXT,
  persona TEXT,                   -- 松下幸之助, 孫正義, ジョブズ, オリジナル
  priority TEXT DEFAULT '中',     -- 高, 中, 低
  status TEXT DEFAULT '新規',     -- 新規, 検討中, 採用, 保留, 却下
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 戦略目標（OKR形式）
CREATE TABLE IF NOT EXISTS no2_strategic_goals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  title TEXT NOT NULL,            -- Objective
  description TEXT,
  target_date DATE,
  status TEXT DEFAULT '計画中',   -- 計画中, 実行中, 達成, 未達
  kpi_ids JSONB DEFAULT '[]',     -- 関連KPIのIDリスト（Key Results）
  progress_pct INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 経営会議
CREATE TABLE IF NOT EXISTS no2_meetings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  meeting_date TIMESTAMPTZ NOT NULL,
  title TEXT,
  topics JSONB DEFAULT '[]',          -- [{content}]
  decisions JSONB DEFAULT '[]',       -- [{content, decided_by}]
  action_items JSONB DEFAULT '[]',    -- [{content, assignee, due_date, done}]
  attendees TEXT[] DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);
```

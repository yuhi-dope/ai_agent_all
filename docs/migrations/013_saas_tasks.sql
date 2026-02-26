-- 013: SaaS BPO タスク管理テーブル
-- AI社員のタスク計画・承認・実行・結果を管理する

CREATE TABLE IF NOT EXISTS saas_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  connection_id UUID REFERENCES company_saas_connections(id),
  task_id TEXT NOT NULL UNIQUE,
  task_description TEXT NOT NULL,
  saas_name TEXT NOT NULL,
  genre TEXT,

  -- 実行計画（LLM 生成）
  plan_markdown TEXT,
  planned_operations JSONB DEFAULT '[]',
  operation_count INT DEFAULT 0,

  -- ステータス
  -- planning → awaiting_approval → executing → completed / failed / rejected
  status TEXT NOT NULL DEFAULT 'planning',
  dry_run BOOLEAN DEFAULT false,

  -- 実行結果（サマリーのみ — 企業の機密データは保存しない）
  result_summary JSONB,
  report_markdown TEXT,
  duration_ms INT,

  -- 失敗追跡（学習システム用）
  failure_reason TEXT,
  failure_category TEXT,
  failure_detail TEXT,

  -- タイムスタンプ
  created_at TIMESTAMPTZ DEFAULT now(),
  approved_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_saas_tasks_company ON saas_tasks(company_id);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_status ON saas_tasks(status);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_saas_name ON saas_tasks(saas_name);
CREATE INDEX IF NOT EXISTS idx_saas_tasks_failure ON saas_tasks(failure_category)
  WHERE failure_category IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_saas_tasks_created ON saas_tasks(created_at DESC);

-- RLS
ALTER TABLE saas_tasks ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'saas_tasks' AND policyname = 'saas_tasks_service_all'
  ) THEN
    CREATE POLICY saas_tasks_service_all ON saas_tasks
      FOR ALL USING (true) WITH CHECK (true);
  END IF;
END
$$;

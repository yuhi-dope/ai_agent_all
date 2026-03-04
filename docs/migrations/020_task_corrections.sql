-- 020: タスク修正履歴テーブル（修正駆動学習用）
-- retry 時のユーザー修正を記録し、意図解釈ルールの自動生成に活用する

CREATE TABLE IF NOT EXISTS task_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    saas_name TEXT NOT NULL,
    genre TEXT,

    -- 修正前データ（retry 時点のスナップショット）
    original_description TEXT NOT NULL,
    original_plan_markdown TEXT,
    original_planned_operations JSONB DEFAULT '[]',
    original_confidence NUMERIC(4,3),
    original_warnings JSONB DEFAULT '[]',
    original_status TEXT,
    original_failure_reason TEXT,
    original_failure_category TEXT,

    -- 修正後データ
    modified_description TEXT NOT NULL,

    -- 修正メタデータ
    correction_type TEXT DEFAULT 'description_change',
    user_notes TEXT,

    -- 最終結果（修正版が成功したか）
    outcome TEXT DEFAULT 'pending',
    outcome_updated_at TIMESTAMPTZ,

    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_corrections_task
    ON task_corrections(task_id);
CREATE INDEX IF NOT EXISTS idx_task_corrections_saas
    ON task_corrections(saas_name);
CREATE INDEX IF NOT EXISTS idx_task_corrections_outcome
    ON task_corrections(outcome);
CREATE INDEX IF NOT EXISTS idx_task_corrections_created
    ON task_corrections(created_at DESC);

ALTER TABLE task_corrections ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'task_corrections' AND policyname = 'task_corrections_service_all'
    ) THEN
        CREATE POLICY task_corrections_service_all ON task_corrections
            FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;

-- 023: 実行ナレッジ蓄積テーブル（実行学習用）
-- 全タスク実行結果を構造化して記録し、ベストプラクティスルール生成に活用する

CREATE TABLE IF NOT EXISTS execution_knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,

    -- SaaS × Genre 軸
    saas_name TEXT NOT NULL,
    genre TEXT,

    -- タスクメタデータ
    task_description TEXT NOT NULL,
    task_type TEXT,                          -- field_creation, record_addition, layout_change 等

    -- 操作シーケンス（構造化 JSONB）
    -- [{tool_name, success, key_params, error, duration_ms}]
    operation_sequence JSONB NOT NULL DEFAULT '[]',
    operation_count INT NOT NULL DEFAULT 0,

    -- 全体結果
    overall_success BOOLEAN NOT NULL,
    success_count INT NOT NULL DEFAULT 0,
    failure_count INT NOT NULL DEFAULT 0,
    duration_ms INT DEFAULT 0,

    -- 計画メタデータ（計画品質との相関分析用）
    plan_confidence NUMERIC(4,3),
    plan_warnings JSONB DEFAULT '[]',

    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exec_knowledge_saas_genre
    ON execution_knowledge(saas_name, genre);
CREATE INDEX IF NOT EXISTS idx_exec_knowledge_task
    ON execution_knowledge(task_id);
CREATE INDEX IF NOT EXISTS idx_exec_knowledge_success
    ON execution_knowledge(overall_success);
CREATE INDEX IF NOT EXISTS idx_exec_knowledge_task_type
    ON execution_knowledge(task_type)
    WHERE task_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_exec_knowledge_created
    ON execution_knowledge(created_at DESC);

ALTER TABLE execution_knowledge ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'execution_knowledge' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON execution_knowledge
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

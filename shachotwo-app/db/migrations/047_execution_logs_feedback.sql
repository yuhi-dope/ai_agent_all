-- execution_logs にフィードバック種別・改善サイクル管理カラムを追加
-- prompt_optimizer / improvement_cycle で使用

ALTER TABLE execution_logs
    -- フィードバック種別:
    --   'prompt_improvement_only': プロンプト改善専用（ナレッジルールには入れない）
    --   'rule_candidate': ルール追加候補（ユーザー承認後にknowledge_itemsへ）
    --   NULL: フィードバックなし
    ADD COLUMN IF NOT EXISTS feedback_type TEXT
        CHECK (feedback_type IN ('prompt_improvement_only', 'rule_candidate')),

    -- ユーザーが明示的にルール追加を承認したか（rule_candidateのみ有効）
    ADD COLUMN IF NOT EXISTS rule_add_confirmed BOOLEAN DEFAULT FALSE,

    -- 改善サイクルが適用された日時（7日以内なら再改善をスキップ）
    ADD COLUMN IF NOT EXISTS improvement_applied_at TIMESTAMPTZ,

    -- 改善サイクルでスキップされた場合の理由
    ADD COLUMN IF NOT EXISTS improvement_skip_reason TEXT;

-- フィードバック種別 × 日時でのフィルタリングに使うインデックス
CREATE INDEX IF NOT EXISTS idx_execution_logs_feedback
    ON execution_logs (company_id, feedback_type, created_at DESC)
    WHERE feedback_type IS NOT NULL;

-- 改善済みスキップ判定用インデックス
CREATE INDEX IF NOT EXISTS idx_execution_logs_improvement
    ON execution_logs (company_id, improvement_applied_at DESC)
    WHERE improvement_applied_at IS NOT NULL;

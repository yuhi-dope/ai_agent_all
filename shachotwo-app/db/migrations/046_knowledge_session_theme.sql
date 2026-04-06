-- knowledge_sessions にBPOテーマ別セッション管理カラムを追加
-- session_manager.py の auto_compact 機能で使用

ALTER TABLE knowledge_sessions
    ADD COLUMN IF NOT EXISTS bpo_theme TEXT,
    ADD COLUMN IF NOT EXISTS question_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS compressed_context TEXT,
    ADD COLUMN IF NOT EXISTS raw_context_archive JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS session_status TEXT NOT NULL DEFAULT 'active'
        CHECK (session_status IN ('active', 'compacted', 'closed'));

-- テーマ別の最新セッション検索に使うインデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_theme
    ON knowledge_sessions (company_id, bpo_theme, session_status, created_at DESC);

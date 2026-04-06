-- Q&A履歴テーブル: 社長の質問→回答を記録し、ナレッジの穴発見・BPO優先度・信頼度調整に活用
CREATE TABLE qa_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT,
    -- 回答に使ったナレッジのID群
    referenced_knowledge_ids UUID[] DEFAULT '{}',
    -- 回答できたか（answered / no_match / partial）
    answer_status TEXT NOT NULL DEFAULT 'answered',
    -- ユーザー評価（任意）
    user_rating TEXT,          -- helpful / wrong / outdated
    user_feedback TEXT,        -- 自由記述「これ違う、今は60時間」
    -- LLMメタ
    model_used TEXT,
    confidence FLOAT,
    cost_yen FLOAT DEFAULT 0,
    -- 検索用embedding（質問のベクトル、類似質問検索・クラスタリング用）
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- インデックス
CREATE INDEX idx_qa_sessions_company ON qa_sessions(company_id);
CREATE INDEX idx_qa_sessions_company_created ON qa_sessions(company_id, created_at DESC);
CREATE INDEX idx_qa_sessions_answer_status ON qa_sessions(company_id, answer_status);

-- RLS
ALTER TABLE qa_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY qa_sessions_select ON qa_sessions
    FOR SELECT USING (company_id = (current_setting('app.company_id', true))::uuid);

CREATE POLICY qa_sessions_insert ON qa_sessions
    FOR INSERT WITH CHECK (company_id = (current_setting('app.company_id', true))::uuid);

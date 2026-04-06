-- 011: knowledge_sessions に LLMコスト追跡カラム追加
-- 月間コスト集計を可能にする

ALTER TABLE knowledge_sessions
  ADD COLUMN IF NOT EXISTS cost_yen FLOAT DEFAULT 0,
  ADD COLUMN IF NOT EXISTS model_used TEXT;

COMMENT ON COLUMN knowledge_sessions.cost_yen IS 'LLM利用コスト（円）';
COMMENT ON COLUMN knowledge_sessions.model_used IS '使用したLLMモデル名';

-- 月間集計用インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_sessions_company_created
  ON knowledge_sessions(company_id, created_at);

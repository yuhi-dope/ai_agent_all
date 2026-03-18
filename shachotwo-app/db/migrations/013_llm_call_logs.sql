-- 013_llm_call_logs.sql
-- LLM呼び出しログ — 成功・失敗両方を記録し、精度改善のPDCAを回す
-- Source of Truth: shachotwo/d_02_フィードバック学習ループ設計.md

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    task_type TEXT NOT NULL,                    -- quantity_extraction / price_estimation / qa / etc.
    model_used TEXT,                            -- gemini-2.5-flash / claude-sonnet-4-5 etc.
    input_text_length INTEGER,                  -- 入力テキストの長さ
    input_summary TEXT,                         -- 入力の要約（最初の200文字等）
    output_text_length INTEGER,                 -- 出力テキストの長さ
    output_summary TEXT,                        -- 出力の要約（最初の200文字等）
    status TEXT NOT NULL DEFAULT 'success'
        CHECK (status IN ('success', 'parse_error', 'partial_recovery', 'api_error', 'safety_block', 'timeout')),
    items_extracted INTEGER,                    -- 抽出件数（extraction系の場合）
    parse_method TEXT,                          -- direct / code_block / bracket_extract / partial_recovery
    error_message TEXT,                         -- エラーメッセージ
    raw_response TEXT,                          -- LLM生レスポンス（デバッグ用。長期保存しない）
    latency_ms INTEGER,                         -- レスポンス時間
    cost_yen DECIMAL(10,4),                     -- 推定コスト
    project_id UUID,                            -- 関連するプロジェクトID（あれば）
    metadata JSONB DEFAULT '{}',                -- その他メタデータ
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLSは company_id が NULL の場合もあるため条件付き
ALTER TABLE llm_call_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "llm_call_logs_read" ON llm_call_logs
    FOR SELECT USING (
        company_id IS NULL
        OR company_id = current_setting('app.company_id', true)::uuid
    );
CREATE POLICY "llm_call_logs_insert" ON llm_call_logs
    FOR INSERT WITH CHECK (true);  -- 全社insertは許可（ログ記録のため）

-- インデックス
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_company ON llm_call_logs(company_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_task ON llm_call_logs(task_type, status);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_created ON llm_call_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_call_logs_errors ON llm_call_logs(task_type)
    WHERE status != 'success';

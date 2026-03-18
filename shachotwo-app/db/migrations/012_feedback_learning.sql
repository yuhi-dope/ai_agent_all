-- 012_feedback_learning.sql
-- フィードバック学習ループ Level 1
-- Source of Truth: shachotwo/d_02_フィードバック学習ループ設計.md
--
-- [衝突確認済み]
-- estimation_items: user_modified / original_ai_price / finalized_at → 新規追加OK
-- unit_price_master: ai_estimated_price / accuracy_rate / used_count → 新規追加OK
-- qa_sessions: user_rating は 008_qa_sessions.sql で TEXT 型定義済み → INTEGER追加は不可。
--              INTEGER評価は user_rating_score カラムとして追加。
--              user_feedback は 008 で定義済み → rating_comment はスキップ（同義カラム）。
--              source_item_ids → 新規追加OK
-- knowledge_items: extraction_modified / original_content → 新規追加OK
-- bpo_approvals: modification_diff / rejection_reason / learned_rule → 新規追加OK

-- =============================================================================
-- 1. 積算 単価フィードバック（estimation_items に追加）
-- =============================================================================
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS user_modified BOOLEAN DEFAULT false;
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS original_ai_price DECIMAL(15,2);
ALTER TABLE estimation_items ADD COLUMN IF NOT EXISTS finalized_at TIMESTAMPTZ;

COMMENT ON COLUMN estimation_items.user_modified IS 'AIの積算をユーザーが修正したかどうか';
COMMENT ON COLUMN estimation_items.original_ai_price IS 'AI推定元の単価（修正前の値を保持）';
COMMENT ON COLUMN estimation_items.finalized_at IS '積算確定日時（確定後の変更を追跡）';

-- =============================================================================
-- 2. 単価マスタに精度追跡カラム追加（unit_price_master に追加）
-- =============================================================================
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS ai_estimated_price DECIMAL(15,2);
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS accuracy_rate DECIMAL(5,4);
ALTER TABLE unit_price_master ADD COLUMN IF NOT EXISTS used_count INTEGER DEFAULT 0;

COMMENT ON COLUMN unit_price_master.ai_estimated_price IS 'AIが推定した単価（実績単価との差分で精度を計算）';
COMMENT ON COLUMN unit_price_master.accuracy_rate IS 'AI推定精度（0.0000〜1.0000）。|実績-推定|/実績で算出';
COMMENT ON COLUMN unit_price_master.used_count IS 'この単価が積算に使われた回数（学習データ量の指標）';

-- =============================================================================
-- 3. 数量抽出フィードバック（新規テーブル）
-- =============================================================================
CREATE TABLE IF NOT EXISTS extraction_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    project_id UUID NOT NULL REFERENCES estimation_projects(id),
    original_items JSONB NOT NULL,
    corrected_items JSONB NOT NULL,
    diff_summary JSONB,
    source_format TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE extraction_feedback ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "extraction_feedback_company_isolation" ON extraction_feedback;
CREATE POLICY "extraction_feedback_company_isolation" ON extraction_feedback
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 4. 工種正規化辞書（新規テーブル）
-- company_id = NULL は全社共通エントリ（シャチョツー運営が管理するマスタ辞書）
-- =============================================================================
CREATE TABLE IF NOT EXISTS term_normalization (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),
    domain TEXT NOT NULL DEFAULT 'construction',
    original_term TEXT NOT NULL,
    normalized_term TEXT NOT NULL,
    occurrence_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(company_id, domain, original_term)
);

ALTER TABLE term_normalization ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "term_normalization_read" ON term_normalization;
CREATE POLICY "term_normalization_read" ON term_normalization
    FOR SELECT USING (
        company_id IS NULL
        OR company_id = current_setting('app.company_id', true)::uuid
    );

DROP POLICY IF EXISTS "term_normalization_write" ON term_normalization;
CREATE POLICY "term_normalization_write" ON term_normalization
    FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true)::uuid);

DROP POLICY IF EXISTS "term_normalization_update" ON term_normalization;
CREATE POLICY "term_normalization_update" ON term_normalization
    FOR UPDATE USING (company_id = current_setting('app.company_id', true)::uuid);

-- =============================================================================
-- 5. Q&A フィードバック（qa_sessions に追加）
-- 注意: user_rating (TEXT) は 008 で定義済み。数値スコアは user_rating_score として追加。
--       user_feedback (TEXT) も 008 で定義済みのため rating_comment は追加しない。
-- =============================================================================
ALTER TABLE qa_sessions ADD COLUMN IF NOT EXISTS user_rating_score INTEGER
    CHECK (user_rating_score IS NULL OR (user_rating_score >= 1 AND user_rating_score <= 5));
ALTER TABLE qa_sessions ADD COLUMN IF NOT EXISTS source_item_ids UUID[];

COMMENT ON COLUMN qa_sessions.user_rating_score IS '数値評価（1=最低〜5=最高）。user_rating(TEXT)と併用';
COMMENT ON COLUMN qa_sessions.source_item_ids IS '回答生成に参照したknowledge_itemsのID群';

-- =============================================================================
-- 6. ナレッジ抽出フィードバック（knowledge_items に追加）
-- =============================================================================
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS extraction_modified BOOLEAN DEFAULT false;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS original_content JSONB;

COMMENT ON COLUMN knowledge_items.extraction_modified IS 'LLM抽出結果をユーザーが編集したかどうか';
COMMENT ON COLUMN knowledge_items.original_content IS 'LLM抽出時の元コンテンツJSON（編集前の状態を保持）';

-- =============================================================================
-- 7. BPO承認フィードバック（bpo_approvals に追加）
-- =============================================================================
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS modification_diff JSONB;
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS rejection_reason TEXT;
ALTER TABLE bpo_approvals ADD COLUMN IF NOT EXISTS learned_rule TEXT;

COMMENT ON COLUMN bpo_approvals.modification_diff IS '承認者が内容を修正した場合の差分JSON（before/after）';
COMMENT ON COLUMN bpo_approvals.rejection_reason IS '却下理由（フィードバック学習の入力として使用）';
COMMENT ON COLUMN bpo_approvals.learned_rule IS '却下・修正パターンから抽出した学習ルールのテキスト';

-- =============================================================================
-- インデックス
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_extraction_feedback_company ON extraction_feedback(company_id);
CREATE INDEX IF NOT EXISTS idx_extraction_feedback_project ON extraction_feedback(project_id);
CREATE INDEX IF NOT EXISTS idx_term_normalization_lookup ON term_normalization(company_id, domain, original_term);
CREATE INDEX IF NOT EXISTS idx_unit_price_master_accuracy ON unit_price_master(company_id, category, accuracy_rate);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_rating_score ON qa_sessions(company_id, user_rating_score)
    WHERE user_rating_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_knowledge_items_extraction_modified ON knowledge_items(company_id)
    WHERE extraction_modified = true;

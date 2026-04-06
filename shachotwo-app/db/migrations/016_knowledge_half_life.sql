-- Migration 016: ナレッジ半減期・精度フィードバック
-- ADR-010に基づく: 長期運用でナレッジが陳腐化しないための基盤
-- Phase 1: 手動TTL設定のみ。Phase 2: 自動圧縮バッチに接続

-- knowledge_itemsに半減期・フィードバックカラムを追加
ALTER TABLE knowledge_items
    ADD COLUMN IF NOT EXISTS half_life_days INT DEFAULT NULL,
        -- NULL = 期限なし。30 = 30日で要確認フラグ
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT NULL,
        -- half_life_daysから自動計算。NULLは永続
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ DEFAULT NULL,
        -- 管理者が「この情報は正確です」と確認した日時
    ADD COLUMN IF NOT EXISTS qa_usage_count INT NOT NULL DEFAULT 0,
        -- このナレッジがQ&Aで引用された回数（重要度指標）
    ADD COLUMN IF NOT EXISTS positive_feedback_count INT NOT NULL DEFAULT 0,
        -- Q&A回答後にユーザーが「役立った」と評価した回数
    ADD COLUMN IF NOT EXISTS negative_feedback_count INT NOT NULL DEFAULT 0;
        -- Q&A回答後にユーザーが「役立たなかった」と評価した回数

-- 期限切れナレッジの高速検索インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_items_expires
    ON knowledge_items (company_id, expires_at)
    WHERE expires_at IS NOT NULL AND is_active = TRUE;

-- よく使われる（高価値な）ナレッジの検索インデックス
CREATE INDEX IF NOT EXISTS idx_knowledge_items_usage
    ON knowledge_items (company_id, qa_usage_count DESC)
    WHERE is_active = TRUE;

-- Q&Aセッションにフィードバックカラムを追加
ALTER TABLE qa_sessions
    ADD COLUMN IF NOT EXISTS feedback TEXT
        CHECK (feedback IN ('helpful', 'not_helpful', NULL)),
    ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cited_knowledge_ids UUID[];
        -- この回答で実際に引用されたナレッジのID一覧

COMMENT ON COLUMN knowledge_items.half_life_days IS
    'ナレッジの有効期間（日数）。毎月更新が必要な単価情報=30, 年次更新=365, 変わらないルール=NULL';

COMMENT ON COLUMN knowledge_items.qa_usage_count IS
    'このナレッジがQ&A検索のtop-kに含まれた回数。使われないナレッジは精度評価の対象外';

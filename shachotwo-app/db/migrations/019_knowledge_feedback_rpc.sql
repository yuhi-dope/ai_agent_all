-- Migration 019: knowledge_items フィードバックカウント インクリメント用 RPC
-- brain/knowledge/qa.py のフィードバック評価（👍👎）から呼び出される。
-- positive_feedback_count または negative_feedback_count を一括インクリメントする。

CREATE OR REPLACE FUNCTION increment_knowledge_feedback(
    item_ids   UUID[],
    count_column TEXT  -- 'positive_feedback_count' or 'negative_feedback_count'
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    -- count_column の値を検証してSQLインジェクションを防ぐ
    IF count_column NOT IN ('positive_feedback_count', 'negative_feedback_count') THEN
        RAISE EXCEPTION 'Invalid count_column: %', count_column;
    END IF;

    IF count_column = 'positive_feedback_count' THEN
        UPDATE knowledge_items
        SET positive_feedback_count = positive_feedback_count + 1
        WHERE id = ANY(item_ids)
          AND is_active = TRUE;
    ELSE
        UPDATE knowledge_items
        SET negative_feedback_count = negative_feedback_count + 1
        WHERE id = ANY(item_ids)
          AND is_active = TRUE;
    END IF;
END;
$$;

-- RLSをバイパスするためSECURITY DEFINERを使用。
-- 呼び出し元（routers/knowledge.py）がservice_roleクライアント経由でのみ呼び出す。
COMMENT ON FUNCTION increment_knowledge_feedback(UUID[], TEXT) IS
    'Q&A評価で引用されたナレッジのpositive/negative_feedback_countを一括インクリメントする。routers/knowledge.pyから呼び出し。';

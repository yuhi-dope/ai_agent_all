-- Migration 017: qa_usage_count インクリメント用RPC関数
-- brain/knowledge/qa.py から呼び出される。
-- Q&A検索で引用されたナレッジのqa_usage_countを一括インクリメントする。

CREATE OR REPLACE FUNCTION increment_qa_usage_count(item_ids UUID[])
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE knowledge_items
    SET qa_usage_count = qa_usage_count + 1
    WHERE id = ANY(item_ids)
      AND is_active = TRUE;
END;
$$;

-- RLSをバイパスするためSECURITY DEFINERを使用。
-- 呼び出し元（brain/knowledge/qa.py）がservice_roleクライアント経由でのみ呼び出す。
COMMENT ON FUNCTION increment_qa_usage_count(UUID[]) IS
    'Q&A検索で引用されたナレッジのqa_usage_countを一括インクリメントする。brain/knowledge/qa.pyから呼び出し。';

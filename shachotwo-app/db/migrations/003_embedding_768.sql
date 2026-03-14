-- Switch embedding from 512 (Voyage) to 768 (Gemini text-embedding-004)

-- Clear existing embeddings (generated with wrong dimensions)
UPDATE knowledge_items SET embedding = NULL;

-- Change column type
ALTER TABLE knowledge_items ALTER COLUMN embedding TYPE VECTOR(768);

-- Recreate HNSW index for 768 dimensions
DROP INDEX IF EXISTS idx_knowledge_items_embedding;
CREATE INDEX idx_knowledge_items_embedding ON knowledge_items
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Recreate RPC function for 768 dimensions
CREATE OR REPLACE FUNCTION match_knowledge_items(
    query_embedding VECTOR(768),
    match_company_id UUID,
    match_department TEXT DEFAULT NULL,
    match_threshold FLOAT DEFAULT 0.5,
    match_count INT DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    content TEXT,
    department TEXT,
    category TEXT,
    item_type TEXT,
    confidence NUMERIC,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ki.id, ki.title, ki.content, ki.department, ki.category,
        ki.item_type, ki.confidence,
        (1 - (ki.embedding <=> query_embedding))::FLOAT AS similarity
    FROM knowledge_items ki
    WHERE ki.company_id = match_company_id
        AND ki.is_active = true
        AND ki.embedding IS NOT NULL
        AND (match_department IS NULL OR ki.department = match_department)
        AND (1 - (ki.embedding <=> query_embedding)) >= match_threshold
    ORDER BY ki.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

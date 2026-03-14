-- Vector search RPC function for pgvector cosine similarity
-- Used by brain/knowledge/search.py

CREATE OR REPLACE FUNCTION match_knowledge_items(
    query_embedding VECTOR(512),
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

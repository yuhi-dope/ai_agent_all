-- leads テーブルの指定カラムから DISTINCT 値を高速取得するRPC関数
-- facets API（大分類→中分類→小分類カスケードフィルタ）で使用
--
-- 使用例:
--   SELECT * FROM distinct_lead_values('company-uuid', 'industry', NULL, NULL);
--   SELECT * FROM distinct_lead_values('company-uuid', 'sub_industry', 'industry', 'manufacturing');

CREATE OR REPLACE FUNCTION distinct_lead_values(
    p_company_id UUID,
    p_column_name TEXT,
    p_filter_column TEXT DEFAULT NULL,
    p_filter_value TEXT DEFAULT NULL
)
RETURNS TABLE(val TEXT) AS $$
DECLARE
    allowed_columns TEXT[] := ARRAY['industry', 'sub_industry', 'tsr_category_large', 'tsr_category_medium', 'tsr_category_small'];
    allowed_filters TEXT[] := ARRAY['industry', 'sub_industry', 'tsr_category_large', 'tsr_category_medium'];
    query TEXT;
BEGIN
    -- SQLインジェクション防止: カラム名をホワイトリストで検証
    IF NOT (p_column_name = ANY(allowed_columns)) THEN
        RAISE EXCEPTION 'Invalid column name: %', p_column_name;
    END IF;

    IF p_filter_column IS NOT NULL AND NOT (p_filter_column = ANY(allowed_filters)) THEN
        RAISE EXCEPTION 'Invalid filter column: %', p_filter_column;
    END IF;

    query := format(
        'SELECT DISTINCT %I::TEXT AS val FROM leads WHERE company_id = $1 AND %I IS NOT NULL',
        p_column_name, p_column_name
    );

    IF p_filter_column IS NOT NULL AND p_filter_value IS NOT NULL THEN
        query := query || format(' AND %I = $2', p_filter_column);
    END IF;

    query := query || ' ORDER BY val';

    IF p_filter_column IS NOT NULL AND p_filter_value IS NOT NULL THEN
        RETURN QUERY EXECUTE query USING p_company_id, p_filter_value;
    ELSE
        RETURN QUERY EXECUTE query USING p_company_id;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

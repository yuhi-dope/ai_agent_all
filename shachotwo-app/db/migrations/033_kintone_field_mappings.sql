-- kintone アプリごとの canonical → kintone フィールドコードマッピング
-- 要件: b_10 §3-3  field_mappings: {"name": "会社名", "corporate_number": "法人番号", ...}

CREATE TABLE IF NOT EXISTS kintone_field_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL,
    field_mappings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, app_id)
);

CREATE INDEX IF NOT EXISTS idx_kintone_field_mappings_company
    ON kintone_field_mappings (company_id);

ALTER TABLE kintone_field_mappings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "kintone_field_mappings_company_isolation" ON kintone_field_mappings;
CREATE POLICY "kintone_field_mappings_company_isolation" ON kintone_field_mappings
    FOR ALL USING (company_id = current_setting('app.company_id', true)::uuid);

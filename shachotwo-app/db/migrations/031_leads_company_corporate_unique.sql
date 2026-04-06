-- leads: (company_id, corporate_number) で upsert するための一意制約
-- corporate_number が NULL／空の行は複数許容（部分ユニークインデックス）

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_company_corporate_unique
ON leads (company_id, corporate_number)
WHERE corporate_number IS NOT NULL AND btrim(corporate_number) <> '';

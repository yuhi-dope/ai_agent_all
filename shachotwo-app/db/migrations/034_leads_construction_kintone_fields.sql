-- 建設業 kintone 取り込み用（b_10 §3-7）
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contractor_license_number TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS permit_expiry_date DATE;

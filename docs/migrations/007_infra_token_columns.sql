-- 007: インフラトークン暗号化カラム + 有効期限を companies テーブルに追加
-- Supabase Management API / Vercel API トークンを暗号化して保存する

ALTER TABLE companies ADD COLUMN IF NOT EXISTS supabase_mgmt_token_enc TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS supabase_mgmt_token_expires_at TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS vercel_token_enc TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS vercel_token_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN companies.supabase_mgmt_token_enc IS 'Fernet 暗号化された Supabase Management API トークン';
COMMENT ON COLUMN companies.supabase_mgmt_token_expires_at IS 'Supabase Management API トークンの有効期限';
COMMENT ON COLUMN companies.vercel_token_enc IS 'Fernet 暗号化された Vercel API トークン';
COMMENT ON COLUMN companies.vercel_token_expires_at IS 'Vercel API トークンの有効期限';

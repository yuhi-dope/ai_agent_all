-- 運営会社テナントの名称変更: 株式会社ブリッジワン → 湊合同会社
-- 管理者ユーザーのメールアドレス変更: sugimoto_yuhi@bridge-one.co.jp → yuhi.sugimoto@minato-holdings.com
--
-- Supabase SQL Editor (postgres ロール) で実行すること
-- company_id: 86ea5be1-6121-4303-b8d4-84c26b7906b6
-- user_id   : b92ab05b-1d5c-417d-90df-2b3c7e0bb639

-- ① companies テーブルの会社名を更新
UPDATE public.companies
SET name = '湊合同会社'
WHERE id = '86ea5be1-6121-4303-b8d4-84c26b7906b6'::uuid;

-- ② public.users テーブルのメールアドレスを更新
UPDATE public.users
SET email = 'y.sugimoto@minato-holdings.com'
WHERE id = 'b92ab05b-1d5c-417d-90df-2b3c7e0bb639'::uuid;

-- ③ auth.users のメールアドレス・表示名を更新
--    （Supabase Auth の認証情報本体。postgres ロールが必要）
UPDATE auth.users
SET
  email                  = 'y.sugimoto@minato-holdings.com',
  email_confirmed_at     = COALESCE(email_confirmed_at, NOW()),
  raw_user_meta_data     = jsonb_set(
                             COALESCE(raw_user_meta_data, '{}'::jsonb),
                             '{email}',
                             '"y.sugimoto@minato-holdings.com"'
                           ),
  updated_at             = NOW()
WHERE id = 'b92ab05b-1d5c-417d-90df-2b3c7e0bb639'::uuid;

-- ④ auth.identities のメールアドレスを更新（email プロバイダー）
UPDATE auth.identities
SET
  identity_data = jsonb_set(
                    identity_data,
                    '{email}',
                    '"y.sugimoto@minato-holdings.com"'
                  ),
  updated_at    = NOW()
WHERE user_id = 'b92ab05b-1d5c-417d-90df-2b3c7e0bb639'::uuid
  AND provider  = 'email';

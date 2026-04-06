-- ============================================================
-- 041_mfa_settings.sql
-- SOC2準備: MFA（多要素認証）設定テーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS public.mfa_settings (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id       UUID NOT NULL,
  -- ユーザーごとに1レコード（UNIQUE制約）
  user_id          UUID NOT NULL UNIQUE,

  -- TOTPシークレット
  -- 重要: 平文で保存しない。アプリレイヤーでAES-256-GCM等で暗号化した値を保存すること。
  -- 暗号化キーはGCP Secret Manager / Supabase Vault で管理する。
  totp_secret      TEXT,

  is_enabled       BOOLEAN DEFAULT false NOT NULL,

  -- バックアップコード（ハッシュ済み: bcrypt/argon2で各コードをハッシュ）
  -- 平文を保存しない。ユーザーへの表示は発行時の一度限り。
  backup_codes     TEXT[],

  last_verified_at TIMESTAMPTZ,
  created_at       TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- ============================================================
-- インデックス
-- ============================================================

-- company_id: テナント別一覧
CREATE INDEX IF NOT EXISTS idx_mfa_settings_company
  ON public.mfa_settings (company_id);

-- ============================================================
-- RLS（Row Level Security）
-- 本人のみ自分の設定をSELECT/UPDATE可能
-- ============================================================

ALTER TABLE public.mfa_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mfa_settings_self_select ON public.mfa_settings;
DROP POLICY IF EXISTS mfa_settings_self_insert ON public.mfa_settings;
DROP POLICY IF EXISTS mfa_settings_self_update ON public.mfa_settings;

-- 本人のみSELECT
CREATE POLICY mfa_settings_self_select ON public.mfa_settings
  FOR SELECT
  USING (user_id = auth.uid());

-- 本人のみINSERT（初回セットアップ）
CREATE POLICY mfa_settings_self_insert ON public.mfa_settings
  FOR INSERT
  WITH CHECK (user_id = auth.uid());

-- 本人のみUPDATE
CREATE POLICY mfa_settings_self_update ON public.mfa_settings
  FOR UPDATE
  USING (user_id = auth.uid());

-- DELETE は禁止（無効化はis_enabled=falseで行う）
-- service_roleはRLS bypassのため管理操作は可能

COMMENT ON TABLE public.mfa_settings IS
  'ユーザーごとのMFA（TOTP）設定。totp_secretはアプリレイヤーで暗号化済みの値を保存。';
COMMENT ON COLUMN public.mfa_settings.totp_secret IS
  '【暗号化必須】平文保存禁止。AES-256-GCMで暗号化後に保存すること。';
COMMENT ON COLUMN public.mfa_settings.backup_codes IS
  '【ハッシュ必須】平文保存禁止。bcrypt/argon2でハッシュ後に保存すること。ユーザーへの表示は発行時1回限り。';

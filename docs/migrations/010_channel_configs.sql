-- 010: Per-tenant channel configuration table
-- クライアント企業ごとにチャネル連携の設定（CLIENT_ID, CLIENT_SECRET 等）を保存する。
-- 設定値は Fernet で暗号化した JSON として config_enc に格納。

CREATE TABLE IF NOT EXISTS channel_configs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  config_enc TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_channel_configs_company
  ON channel_configs(company_id);

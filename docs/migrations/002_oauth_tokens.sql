-- 002_oauth_tokens.sql
-- OAuth トークン管理テーブル（Notion / Slack / Google Drive / Chatwork）

CREATE TABLE IF NOT EXISTS oauth_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,             -- 'notion', 'slack', 'gdrive', 'chatwork'
  tenant_id TEXT NOT NULL DEFAULT 'default',
  access_token TEXT NOT NULL,
  refresh_token TEXT,
  token_type TEXT DEFAULT 'Bearer',
  expires_at TIMESTAMPTZ,
  scopes TEXT,                        -- スペース区切り
  raw_response JSONB,                 -- OAuth レスポンス全体（デバッグ用）
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_tokens_provider_tenant
  ON oauth_tokens(provider, tenant_id);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider
  ON oauth_tokens(provider);

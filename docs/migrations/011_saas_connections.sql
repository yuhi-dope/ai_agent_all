-- SaaS MCP 接続管理テーブル（Phase 1: SaaS統合エージェント基盤）
-- 企業ごとのSaaS接続情報を管理する。トークン本体は GCP Secret Manager に保管し、
-- DBにはシークレット名のみを格納する。

CREATE TABLE IF NOT EXISTS company_saas_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  saas_name TEXT NOT NULL,             -- 'salesforce', 'freee', 'kintone' 等
  genre TEXT NOT NULL,                 -- 'sfa', 'accounting' 等（10ジャンル）
  department TEXT,                     -- '営業部', '経理部' 等（任意）
  auth_method TEXT NOT NULL,           -- 'oauth2', 'api_key', 'basic'
  token_secret_name TEXT,              -- GCP Secret Manager のシークレット名（トークン本体はSMに保管）
  mcp_server_type TEXT NOT NULL,       -- 'official', 'community', 'custom'
  mcp_server_config JSONB,             -- MCP接続設定（URL, transport等）
  instance_url TEXT,                   -- SaaSのインスタンスURL（Salesforce, kintone, SmartHR等）
  scopes TEXT[],                       -- 許可されたOAuthスコープ
  status TEXT NOT NULL DEFAULT 'active', -- 'active', 'token_expired', 'disconnected', 'error'
  error_message TEXT,                  -- エラー時のメッセージ
  connected_at TIMESTAMPTZ DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  last_health_check_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (company_id, saas_name, department)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_csc_company ON company_saas_connections(company_id);
CREATE INDEX IF NOT EXISTS idx_csc_saas ON company_saas_connections(saas_name);
CREATE INDEX IF NOT EXISTS idx_csc_genre ON company_saas_connections(genre);
CREATE INDEX IF NOT EXISTS idx_csc_status ON company_saas_connections(status);

-- RLS（Row Level Security）ポリシー
ALTER TABLE company_saas_connections ENABLE ROW LEVEL SECURITY;

-- サービスキーは全行アクセス可（サーバーサイド用）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'company_saas_connections' AND policyname = 'csc_service_all'
  ) THEN
    CREATE POLICY csc_service_all ON company_saas_connections
      FOR ALL
      USING (true)
      WITH CHECK (true);
  END IF;
END
$$;

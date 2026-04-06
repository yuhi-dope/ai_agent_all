-- 025: Google Workspace 双方向同期基盤テーブル
-- watch_channels: Gmail/Calendar Watch APIチャネル管理
-- gws_sync_state: DB→GWS逆同期の冪等性管理

-- =====================================================================
-- watch_channels — Gmail Watch / Calendar Watch のチャネル管理
-- =====================================================================
CREATE TABLE IF NOT EXISTS watch_channels (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    service         TEXT NOT NULL CHECK (service IN ('gmail', 'calendar')),
    channel_id      TEXT NOT NULL UNIQUE,
    resource_id     TEXT,                   -- Google API が返す resourceId
    history_id      TEXT,                   -- Gmail: 最後に処理した historyId
    calendar_id     TEXT,                   -- Calendar の場合のみ (例: "primary")
    expiration      TIMESTAMPTZ NOT NULL,   -- Watch の有効期限
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_watch_channels_company
    ON watch_channels(company_id);
CREATE INDEX IF NOT EXISTS idx_watch_channels_expiration
    ON watch_channels(expiration) WHERE is_active = true;

ALTER TABLE watch_channels ENABLE ROW LEVEL SECURITY;
CREATE POLICY watch_channels_tenant_isolation ON watch_channels
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- gws_sync_state — パイプライン結果→GWS反映の冪等性管理
-- =====================================================================
CREATE TABLE IF NOT EXISTS gws_sync_state (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    sync_id         TEXT NOT NULL UNIQUE,    -- execution_log_id + sync_type のハッシュ
    sync_type       TEXT NOT NULL,           -- 'sheets' | 'calendar' | 'drive' | 'gmail_draft'
    source_pipeline TEXT NOT NULL,           -- 元パイプライン名
    target_resource TEXT,                    -- spreadsheet_id / calendar_id / folder_id 等
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'synced', 'failed', 'skipped')),
    last_synced_at  TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gws_sync_state_pending
    ON gws_sync_state(company_id, status) WHERE status = 'pending';

ALTER TABLE gws_sync_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY gws_sync_state_tenant_isolation ON gws_sync_state
    USING (company_id = current_setting('app.company_id')::uuid);

-- 014: 操作パターン検出テーブル
-- Pattern Detector (Phase 2) が検出した操作パターンを蓄積する

CREATE TABLE IF NOT EXISTS operation_patterns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  pattern_type TEXT NOT NULL,            -- 'frequency', 'sequence', 'field_mapping', 'schema_diff'
  saas_name TEXT,
  genre TEXT,
  pattern_key TEXT NOT NULL,             -- パターンの一意キー（重複検出用）
  title TEXT NOT NULL,
  description TEXT,
  pattern_data JSONB NOT NULL DEFAULT '{}',
  occurrence_count INT NOT NULL DEFAULT 1,
  confidence REAL NOT NULL DEFAULT 0.0,  -- 0.0〜1.0
  status TEXT NOT NULL DEFAULT 'detected',  -- 'detected', 'confirmed', 'dismissed'
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_op_patterns_company ON operation_patterns(company_id);
CREATE INDEX IF NOT EXISTS idx_op_patterns_type ON operation_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_op_patterns_key ON operation_patterns(pattern_key);
CREATE INDEX IF NOT EXISTS idx_op_patterns_status ON operation_patterns(status);

-- SaaS スキーマスナップショット（Phase 2 スキーマ差分検出用）
CREATE TABLE IF NOT EXISTS saas_schema_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  connection_id UUID,
  saas_name TEXT NOT NULL,
  schema_data JSONB NOT NULL DEFAULT '{}',   -- オブジェクト・フィールド・型の構造
  snapshot_hash TEXT NOT NULL,                -- schema_data のハッシュ（差分検出用）
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schema_snap_company ON saas_schema_snapshots(company_id);
CREATE INDEX IF NOT EXISTS idx_schema_snap_saas ON saas_schema_snapshots(saas_name);
CREATE INDEX IF NOT EXISTS idx_schema_snap_created ON saas_schema_snapshots(created_at DESC);

-- RLS
ALTER TABLE operation_patterns ENABLE ROW LEVEL SECURITY;
CREATE POLICY op_patterns_service_all ON operation_patterns
  FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE saas_schema_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY schema_snap_service_all ON saas_schema_snapshots
  FOR ALL USING (true) WITH CHECK (true);

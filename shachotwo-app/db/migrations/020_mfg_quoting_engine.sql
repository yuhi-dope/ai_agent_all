-- 020: 製造業見積3層エンジン対応
-- mfg_quotesに業種・レイヤー情報を追加

ALTER TABLE mfg_quotes
  ADD COLUMN IF NOT EXISTS sub_industry TEXT DEFAULT 'metalwork',
  ADD COLUMN IF NOT EXISTS layers_used JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS overall_confidence DECIMAL(3,2),
  ADD COLUMN IF NOT EXISTS additional_costs JSONB DEFAULT '[]';

-- mfg_quote_itemsにレイヤーソース追加
ALTER TABLE mfg_quote_items
  ADD COLUMN IF NOT EXISTS layer_source TEXT DEFAULT 'yaml';

-- user_modifiedカラムが存在しない場合に追加
ALTER TABLE mfg_quote_items
  ADD COLUMN IF NOT EXISTS user_modified BOOLEAN DEFAULT false;

-- 学習ループ用の集約ビュー
CREATE OR REPLACE VIEW mfg_historical_averages AS
SELECT
  company_id,
  equipment_type,
  COUNT(*) as sample_count,
  AVG(setup_time_min) FILTER (WHERE user_modified = true) as avg_setup_min,
  AVG(cycle_time_min) FILTER (WHERE user_modified = true) as avg_cycle_min,
  AVG(confidence) as avg_confidence
FROM mfg_quote_items
WHERE user_modified = true
  AND created_at > now() - interval '6 months'
GROUP BY company_id, equipment_type
HAVING COUNT(*) >= 3;

-- インデックス
CREATE INDEX IF NOT EXISTS idx_mfg_quotes_sub_industry
  ON mfg_quotes(company_id, sub_industry);
CREATE INDEX IF NOT EXISTS idx_mfg_quote_items_modified
  ON mfg_quote_items(company_id, equipment_type, user_modified)
  WHERE user_modified = true;

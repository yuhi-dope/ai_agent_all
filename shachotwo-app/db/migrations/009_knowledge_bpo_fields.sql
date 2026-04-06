-- knowledge_items に BPO関連フィールドとソースタグを追加
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS source_tag TEXT;          -- "common" or "construction" etc.
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS bpo_automatable BOOLEAN DEFAULT false;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS bpo_method TEXT;           -- SaaS名・自動化手法

CREATE INDEX IF NOT EXISTS idx_knowledge_items_bpo ON knowledge_items(company_id, bpo_automatable) WHERE bpo_automatable = true;

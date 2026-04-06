-- オントロジー（Knowledge Graph）テーブル群
-- migration: 039_knowledge_graph.sql

-- ─────────────────────────────────────────────
-- エンティティテーブル
-- ─────────────────────────────────────────────
CREATE TABLE kg_entities (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id       UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    entity_type      TEXT        NOT NULL CHECK (entity_type IN ('Company','Person','Project','Contract','Product','Transaction','Document','Task')),
    entity_key       TEXT        NOT NULL,   -- 外部システムの一意キー（connector名:id）
    display_name     TEXT        NOT NULL,
    properties       JSONB       DEFAULT '{}'::jsonb,
    embedding        vector(768),
    source_connector TEXT,                   -- どのコネクタから来たか
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(company_id, entity_key)
);

ALTER TABLE kg_entities ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON kg_entities
    FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE INDEX idx_kg_entities_company_type ON kg_entities(company_id, entity_type);
CREATE INDEX idx_kg_entities_embedding    ON kg_entities USING hnsw(embedding vector_cosine_ops);

-- ─────────────────────────────────────────────
-- 関係テーブル
-- ─────────────────────────────────────────────
CREATE TABLE kg_relations (
    id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id       UUID        NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    from_entity_id   UUID        NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    relation_type    TEXT        NOT NULL CHECK (relation_type IN ('BELONGS_TO','OWNS','RELATED_TO','SUPPLIED_BY','EXECUTED_BY','DERIVED_FROM','DEPENDS_ON')),
    to_entity_id     UUID        NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    properties       JSONB       DEFAULT '{}'::jsonb,
    confidence_score FLOAT       DEFAULT 1.0,
    source           TEXT        DEFAULT 'manual' CHECK (source IN ('manual','auto_extracted','connector')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE kg_relations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON kg_relations
    FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE INDEX idx_kg_relations_from ON kg_relations(company_id, from_entity_id);
CREATE INDEX idx_kg_relations_to   ON kg_relations(company_id, to_entity_id);

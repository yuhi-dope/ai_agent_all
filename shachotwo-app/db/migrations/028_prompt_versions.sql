-- 027_prompt_versions.sql
-- プロンプトバージョン管理テーブル
-- PromptVersion dataclass のDB永続化・改善履歴トラッキング用

CREATE TABLE IF NOT EXISTS prompt_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID REFERENCES companies(id),  -- NULLなら全社共通
    pipeline TEXT NOT NULL,
    step_name TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    prompt_text TEXT NOT NULL,
    accuracy_before NUMERIC(4,3),
    accuracy_after NUMERIC(4,3),
    change_reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- pipeline+step_name で高速ルックアップ
CREATE INDEX IF NOT EXISTS idx_prompt_versions_lookup
    ON prompt_versions(pipeline, step_name, is_active);

-- 全社共通のアクティブバージョンはpipeline+step_nameで1件のみ許可
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_active
    ON prompt_versions(pipeline, step_name)
    WHERE is_active = TRUE AND company_id IS NULL;

-- RLS有効化
ALTER TABLE prompt_versions ENABLE ROW LEVEL SECURITY;

-- サービスロールは全件アクセス可
CREATE POLICY "service_role_all" ON prompt_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 認証ユーザーは自社データのみ参照・操作可（NULLは全社共通として全員参照可）
CREATE POLICY "tenant_select" ON prompt_versions
    FOR SELECT TO authenticated
    USING (company_id IS NULL OR company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));

CREATE POLICY "tenant_insert" ON prompt_versions
    FOR INSERT TO authenticated
    WITH CHECK (company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));

CREATE POLICY "tenant_update" ON prompt_versions
    FOR UPDATE TO authenticated
    USING (company_id = (
        SELECT company_id FROM users WHERE id = auth.uid() LIMIT 1
    ));

-- 029: BPO導入事例DB — 営業提案用「御社と似た会社ではこういう効果が出ました」
-- bpo_case_studies: 事例基本情報
-- bpo_case_milestones: 月次マイルストーン
-- bpo_case_tags: 検索用タグ

-- =====================================================================
-- bpo_case_studies — 導入事例の基本情報
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_studies (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    industry_code       TEXT NOT NULL,
        -- 製造業: 切削 / 板金 / 射出 / 鋳造 / 食品 / 組立 / 溶接 / 塗装
        -- 建設業: 土木 / 建築 / 設備 / 電気 / 管工事
        -- 医療福祉: 歯科 / クリニック / 介護 / 訪看
        -- 不動産: 賃貸管理 / 売買仲介 / PM
        -- 物流: 倉庫 / 運送 / 3PL
        -- 卸売: 食品卸 / 機械部品卸 / 建材卸
    employee_count      INT,
    annual_revenue      BIGINT,                   -- 円単位
    challenge_category  TEXT NOT NULL,
        -- 設備保全 / 人材 / DX / 品質 / 見積 / 原価管理 / 在庫 / 安全 / 営業 / バックオフィス
    challenge_description TEXT NOT NULL,
    solution_description  TEXT NOT NULL,
    bpo_plan            TEXT NOT NULL CHECK (bpo_plan IN ('common', 'industry_specific')),
    monthly_fee         INT,                      -- 月額料金（円）
    before_monthly_hours NUMERIC(8,1),            -- 導入前: 月間作業時間
    after_monthly_hours  NUMERIC(8,1),            -- 導入後: 月間作業時間
    before_annual_cost   BIGINT,                  -- 導入前: 年間コスト（円）
    after_annual_cost    BIGINT,                  -- 導入後: 年間コスト（円）
    annual_savings       BIGINT,                  -- 年間削減額（円）
    roi_months           NUMERIC(4,1),            -- 投資回収期間（月）
    start_date           DATE,
    status               TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'completed')),
    is_public            BOOLEAN NOT NULL DEFAULT false,  -- 外部公開可の匿名事例
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_case_studies_company
    ON bpo_case_studies(company_id);
CREATE INDEX IF NOT EXISTS idx_case_studies_industry
    ON bpo_case_studies(industry_code);
CREATE INDEX IF NOT EXISTS idx_case_studies_challenge
    ON bpo_case_studies(challenge_category);

ALTER TABLE bpo_case_studies ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_studies_tenant_isolation ON bpo_case_studies
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- bpo_case_milestones — 導入の月次マイルストーン
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_milestones (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    case_id             UUID NOT NULL REFERENCES bpo_case_studies(id) ON DELETE CASCADE,
    month_number        INT NOT NULL CHECK (month_number BETWEEN 1 AND 12),
    milestone_description TEXT NOT NULL,
    actual_savings      BIGINT,                   -- その月の削減額（円）
    cumulative_savings  BIGINT,                   -- 累計削減額（円）
    satisfaction_score  INT CHECK (satisfaction_score BETWEEN 1 AND 5),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (case_id, month_number)
);

CREATE INDEX IF NOT EXISTS idx_case_milestones_case
    ON bpo_case_milestones(case_id);

ALTER TABLE bpo_case_milestones ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_milestones_tenant_isolation ON bpo_case_milestones
    USING (company_id = current_setting('app.company_id')::uuid);

-- =====================================================================
-- bpo_case_tags — 事例の検索用タグ
-- =====================================================================
CREATE TABLE IF NOT EXISTS bpo_case_tags (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    case_id             UUID NOT NULL REFERENCES bpo_case_studies(id) ON DELETE CASCADE,
    tag_name            TEXT NOT NULL,
        -- 例: '5軸MC', 'IATF16949', '外国人材', 'ISO9001', 'HACCP',
        --     '多品種少量', 'JIT納品', '24h稼働', '海外取引'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (case_id, tag_name)
);

CREATE INDEX IF NOT EXISTS idx_case_tags_case
    ON bpo_case_tags(case_id);
CREATE INDEX IF NOT EXISTS idx_case_tags_name
    ON bpo_case_tags(tag_name);

ALTER TABLE bpo_case_tags ENABLE ROW LEVEL SECURITY;
CREATE POLICY bpo_case_tags_tenant_isolation ON bpo_case_tags
    USING (company_id = current_setting('app.company_id')::uuid);

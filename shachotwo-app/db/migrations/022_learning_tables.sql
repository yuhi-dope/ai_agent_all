-- 022_learning_tables.sql
-- 学習フィードバックループ テーブル群
-- Source of Truth: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md §4.10

-- =============================================================================
-- 1. win_loss_patterns — 受注/失注パターン（学習データ）
-- =============================================================================
CREATE TABLE win_loss_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    opportunity_id UUID NOT NULL REFERENCES opportunities(id),

    outcome TEXT NOT NULL,                    -- won / lost
    industry TEXT,                            -- 業種
    employee_range TEXT,                      -- 規模帯
    lead_source TEXT,                         -- リードソース
    sales_cycle_days INTEGER,                 -- セールスサイクル日数
    selected_modules JSONB,                   -- 選択モジュール
    lost_reason TEXT,                         -- 失注理由（lostの場合）
    win_factors JSONB,                        -- 受注要因（wonの場合）
    proposal_version_id UUID,                 -- 使用した提案書

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 2. outreach_performance — アウトリーチ成果（PDCA用）
-- =============================================================================
CREATE TABLE outreach_performance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),

    period DATE NOT NULL,                     -- 集計期間（日次）
    industry TEXT NOT NULL,                   -- 業種

    -- 漏斗指標
    researched_count INTEGER DEFAULT 0,       -- リサーチ数
    outreached_count INTEGER DEFAULT 0,       -- アウトリーチ数
    lp_viewed_count INTEGER DEFAULT 0,        -- LP閲覧数
    lead_converted_count INTEGER DEFAULT 0,   -- リード化数
    meeting_booked_count INTEGER DEFAULT 0,   -- 商談予約数

    -- メールA/Bテスト
    email_variant TEXT,                       -- メールバリアント名
    open_rate FLOAT,                          -- 開封率
    click_rate FLOAT,                         -- クリック率

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 3. cs_feedback — CS学習データ
-- =============================================================================
CREATE TABLE cs_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),
    ticket_id UUID NOT NULL REFERENCES support_tickets(id),

    ai_response TEXT,                         -- AI生成回答
    human_correction TEXT,                    -- 人間による修正（あれば）
    csat_score INTEGER,                       -- 顧客満足度
    was_escalated BOOLEAN DEFAULT FALSE,

    -- 学習判定
    quality_label TEXT,                       -- good / needs_improvement / bad
    improvement_applied BOOLEAN DEFAULT FALSE, -- FAQに反映済みか

    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- 4. scoring_model_versions — スコアリングモデルバージョン管理
-- =============================================================================
CREATE TABLE scoring_model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL REFERENCES companies(id),

    model_type TEXT NOT NULL,                 -- lead_score / health_score / upsell_timing
    version INTEGER NOT NULL,
    weights JSONB NOT NULL,                   -- スコアリング重み
    performance_metrics JSONB,                -- 精度指標（適合率・再現率等）

    active BOOLEAN DEFAULT FALSE,             -- 現在使用中か
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- インデックス
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_win_loss_company ON win_loss_patterns(company_id);
CREATE INDEX IF NOT EXISTS idx_win_loss_industry ON win_loss_patterns(industry, outcome);
CREATE INDEX IF NOT EXISTS idx_win_loss_opportunity ON win_loss_patterns(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_outreach_perf_company ON outreach_performance(company_id);
CREATE INDEX IF NOT EXISTS idx_outreach_perf ON outreach_performance(period, industry);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_company ON cs_feedback(company_id);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_ticket ON cs_feedback(ticket_id);
CREATE INDEX IF NOT EXISTS idx_cs_feedback_quality ON cs_feedback(quality_label);
CREATE INDEX IF NOT EXISTS idx_scoring_model_company ON scoring_model_versions(company_id);
CREATE INDEX IF NOT EXISTS idx_scoring_model_active ON scoring_model_versions(company_id, model_type) WHERE active = TRUE;

-- =============================================================================
-- RLS（将来用 — パイロット後に有効化）
-- =============================================================================
-- 全テーブルに company_id ベースの RLS を適用する。
-- パターン:
--   ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY "tenant_isolation" ON {table}
--     USING (company_id = current_setting('app.company_id', true)::uuid);
--
-- 対象テーブル:
--   win_loss_patterns, outreach_performance, cs_feedback, scoring_model_versions

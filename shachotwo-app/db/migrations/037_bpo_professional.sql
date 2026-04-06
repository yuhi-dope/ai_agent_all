-- 士業BPO（社労士・税理士・行政書士・弁護士）テーブル群
-- migration: 037_bpo_professional.sql

-- ─────────────────────────────────────────────
-- 士業パイプライン実行ログ（全業種共通）
-- ─────────────────────────────────────────────
CREATE TABLE professional_pipeline_logs (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        UUID        NOT NULL REFERENCES companies(id),
  pipeline_type     TEXT        NOT NULL,  -- procedure_generation / bookkeeping_check / permit_generation / contract_review / deadline_management
  profession_type   TEXT        NOT NULL,  -- labor_consultant / tax_accountant / administrative_scribe / lawyer / common
  input_summary     JSONB       DEFAULT '{}',
  output_summary    JSONB       DEFAULT '{}',
  is_valid          BOOLEAN     DEFAULT true,
  warnings_count    INTEGER     DEFAULT 0,
  execution_time_ms INTEGER,
  created_at        TIMESTAMPTZ DEFAULT now()
);

-- ─────────────────────────────────────────────
-- 社労士: 手続き管理テーブル
-- ─────────────────────────────────────────────
CREATE TABLE sr_procedures (
  id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id           UUID        NOT NULL REFERENCES companies(id),
  procedure_type       TEXT        NOT NULL,
  procedure_name       TEXT        NOT NULL,
  employee_name        TEXT,
  status               TEXT        DEFAULT 'draft',  -- draft / reviewing / submitted / completed
  deadline_date        DATE,
  form_data            JSONB       DEFAULT '{}',
  compliance_warnings  JSONB       DEFAULT '[]',
  submitted_at         TIMESTAMPTZ,
  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now()
);

-- ─────────────────────────────────────────────
-- 税理士: 記帳チェック結果テーブル
-- ─────────────────────────────────────────────
CREATE TABLE tx_bookkeeping_checks (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id          UUID        NOT NULL REFERENCES companies(id),
  client_company_name TEXT        NOT NULL,
  period_year         INTEGER     NOT NULL,
  period_month        INTEGER     NOT NULL,
  journal_count       INTEGER     DEFAULT 0,
  error_count         INTEGER     DEFAULT 0,
  warning_count       INTEGER     DEFAULT 0,
  check_items         JSONB       DEFAULT '[]',
  created_at          TIMESTAMPTZ DEFAULT now()
);

-- ─────────────────────────────────────────────
-- 行政書士: 許可申請管理テーブル
-- ─────────────────────────────────────────────
CREATE TABLE gy_permit_applications (
  id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id              UUID        NOT NULL REFERENCES companies(id),
  permit_type             TEXT        NOT NULL,
  permit_name             TEXT        NOT NULL,
  applicant_company_name  TEXT        NOT NULL,
  status                  TEXT        DEFAULT 'draft',  -- draft / reviewing / submitted / approved / rejected
  all_requirements_met    BOOLEAN     DEFAULT false,
  requirement_checks      JSONB       DEFAULT '[]',
  documents               JSONB       DEFAULT '[]',
  compliance_warnings     JSONB       DEFAULT '[]',
  submitted_at            TIMESTAMPTZ,
  created_at              TIMESTAMPTZ DEFAULT now(),
  updated_at              TIMESTAMPTZ DEFAULT now()
);

-- ─────────────────────────────────────────────
-- 弁護士: 契約書レビュー結果テーブル
-- ─────────────────────────────────────────────
CREATE TABLE lw_contract_reviews (
  id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id         UUID        NOT NULL REFERENCES companies(id),
  contract_type      TEXT        NOT NULL,
  contract_type_name TEXT        NOT NULL,
  counterparty       TEXT,
  risk_score         INTEGER     DEFAULT 0,
  high_risks         INTEGER     DEFAULT 0,
  medium_risks       INTEGER     DEFAULT 0,
  risks              JSONB       DEFAULT '[]',
  suggestions        JSONB       DEFAULT '[]',
  created_at         TIMESTAMPTZ DEFAULT now()
);

-- ─────────────────────────────────────────────
-- インデックス
-- ─────────────────────────────────────────────
CREATE INDEX idx_professional_pipeline_logs_company_id
  ON professional_pipeline_logs(company_id);
CREATE INDEX idx_professional_pipeline_logs_pipeline_type
  ON professional_pipeline_logs(pipeline_type);
CREATE INDEX idx_professional_pipeline_logs_created_at
  ON professional_pipeline_logs(created_at DESC);

CREATE INDEX idx_sr_procedures_company_id   ON sr_procedures(company_id);
CREATE INDEX idx_sr_procedures_status       ON sr_procedures(status);
CREATE INDEX idx_sr_procedures_deadline     ON sr_procedures(deadline_date);

CREATE INDEX idx_tx_bookkeeping_checks_company_id ON tx_bookkeeping_checks(company_id);
CREATE INDEX idx_tx_bookkeeping_checks_period
  ON tx_bookkeeping_checks(company_id, period_year, period_month);

CREATE INDEX idx_gy_permit_applications_company_id ON gy_permit_applications(company_id);
CREATE INDEX idx_gy_permit_applications_status     ON gy_permit_applications(status);

CREATE INDEX idx_lw_contract_reviews_company_id ON lw_contract_reviews(company_id);
CREATE INDEX idx_lw_contract_reviews_risk_score
  ON lw_contract_reviews(company_id, risk_score DESC);

-- ─────────────────────────────────────────────
-- RLS
-- ─────────────────────────────────────────────
ALTER TABLE professional_pipeline_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sr_procedures              ENABLE ROW LEVEL SECURITY;
ALTER TABLE tx_bookkeeping_checks      ENABLE ROW LEVEL SECURITY;
ALTER TABLE gy_permit_applications     ENABLE ROW LEVEL SECURITY;
ALTER TABLE lw_contract_reviews        ENABLE ROW LEVEL SECURITY;

CREATE POLICY "company_isolation" ON professional_pipeline_logs
  FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE POLICY "company_isolation" ON sr_procedures
  FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE POLICY "company_isolation" ON tx_bookkeeping_checks
  FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE POLICY "company_isolation" ON gy_permit_applications
  FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

CREATE POLICY "company_isolation" ON lw_contract_reviews
  FOR ALL USING (company_id = current_setting('app.company_id')::uuid);

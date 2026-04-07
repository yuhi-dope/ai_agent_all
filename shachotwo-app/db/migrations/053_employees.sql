-- =============================================================================
-- 052_employees.sql
-- 従業員マスタテーブル
-- =============================================================================
--
-- 目的:
--   社員情報を一元管理するマスタテーブル。
--   給与計算・勤怠管理・労務手続き（社会保険/入退社）の全パイプラインが
--   このテーブルをデータ基盤として参照・更新する。
--
-- 使用パイプライン:
--   - workers/base/payroll_pipeline.py       (給与計算)
--   - workers/base/attendance_pipeline.py    (勤怠管理)
--   - workers/base/hr_procedure_pipeline.py  (労務手続き: 入退社・社保手続き)
--   - workers/industry/                      (業種特化: 建設業 現場作業員管理 等)
--
-- RLS設計:
--   company_id = current_setting('app.company_id', true)::UUID
--   全ての参照・更新は自テナントのレコードのみに制限される。
--   銀行口座・年金番号等のPII項目はアプリ層で追加アクセス制御を行うこと。
--
-- 参照テーブル:
--   companies (id) — テナント
--   users (id)     — システムアカウントとの任意紐付け
-- =============================================================================

-- =============================================================================
-- Table
-- =============================================================================

CREATE TABLE employees (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,  -- システムアカウントとの紐付け（任意）
    employee_code TEXT,          -- 社員番号（例: EMP-001）
    name TEXT NOT NULL,          -- 氏名
    name_kana TEXT,              -- 氏名カナ
    email TEXT,                  -- 業務メール
    department TEXT,             -- 部署
    position TEXT,               -- 役職
    employment_type TEXT NOT NULL DEFAULT 'full_time'
        CHECK (employment_type IN ('full_time', 'part_time', 'contract', 'dispatch')),
    hire_date DATE,              -- 入社日
    resignation_date DATE,       -- 退社日（在籍中はNULL）
    base_salary INTEGER,         -- 基本給（円）
    hourly_wage INTEGER,         -- 時給（パート・アルバイト用）
    scheduled_work_hours NUMERIC(4,1) DEFAULT 8.0,  -- 所定労働時間/日
    paid_leave_days INTEGER DEFAULT 0,  -- 有給残日数
    social_insurance_enrolled BOOLEAN DEFAULT true,  -- 社会保険加入
    health_insurance_number TEXT,  -- 健康保険番号（PII）
    pension_number TEXT,         -- 基礎年金番号（PII）
    bank_account JSONB,          -- 給与振込口座 {bank, branch, account_type, account_number}（PII）
    emergency_contact JSONB,     -- 緊急連絡先 {name, relationship, phone}
    is_active BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE employees IS '従業員マスタ。給与・勤怠・労務パイプラインのデータ基盤。';
COMMENT ON COLUMN employees.employee_code IS '社員番号。テナント内ユニーク。例: EMP-001';
COMMENT ON COLUMN employees.employment_type IS '雇用形態: full_time/part_time/contract/dispatch';
COMMENT ON COLUMN employees.scheduled_work_hours IS '所定労働時間（時間/日）。残業計算の基準値。';
COMMENT ON COLUMN employees.health_insurance_number IS 'PII: アプリ層でアクセス制御すること';
COMMENT ON COLUMN employees.pension_number IS 'PII: アプリ層でアクセス制御すること';
COMMENT ON COLUMN employees.bank_account IS 'PII: {bank, branch, account_type, account_number}';
COMMENT ON COLUMN employees.metadata IS '業種特化・カスタム項目の拡張用JSONB';

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX idx_employees_company
    ON employees (company_id);

CREATE INDEX idx_employees_user
    ON employees (user_id);

CREATE INDEX idx_employees_active
    ON employees (company_id, is_active);

-- employee_code はテナント内ユニーク（NULLは除外）
CREATE UNIQUE INDEX idx_employees_code
    ON employees (company_id, employee_code)
    WHERE employee_code IS NOT NULL;

-- =============================================================================
-- RLS（Row Level Security）
-- =============================================================================

ALTER TABLE employees ENABLE ROW LEVEL SECURITY;

CREATE POLICY "employees_tenant_isolation" ON employees
    USING (company_id = (current_setting('app.company_id', true))::UUID);

-- =============================================================================
-- Trigger: updated_at 自動更新
-- update_updated_at() 関数は 001_initial_schema.sql で定義済み
-- =============================================================================

CREATE TRIGGER trg_employees_updated_at
    BEFORE UPDATE ON employees
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

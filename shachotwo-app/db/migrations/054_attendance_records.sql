-- =============================================================================
-- 054_attendance_records.sql
-- 勤怠記録テーブル
-- =============================================================================
--
-- 目的:
--   従業員の日次勤怠（出退勤・休暇・残業）を記録するトランザクションテーブル。
--   1日1レコード制約により二重記録を防止し、月次集計ビューを通じて
--   給与計算パイプラインに総労働時間・残業時間・有給消化数を提供する。
--
-- 使用パイプライン:
--   - workers/base/attendance_pipeline.py    (勤怠入力・承認)
--   - workers/base/payroll_pipeline.py       (給与計算: attendance_monthly_summary を参照)
--   - workers/micro/anomaly_detector.py      (残業時間の異常検知)
--   - workers/micro/compliance.py            (36協定違反チェック)
--
-- RLS設計:
--   company_id = current_setting('app.company_id', true)::UUID
--   attendance_records は employees 経由でも company_id を保持し、
--   直接テナント分離を実現する（JOINなしでRLSが機能する）。
--
-- 参照テーブル:
--   companies (id)  — テナント
--   employees (id)  — 従業員マスタ (053_employees.sql)
--   users (id)      — 承認者
-- =============================================================================

-- =============================================================================
-- Table
-- =============================================================================

CREATE TABLE attendance_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    work_date DATE NOT NULL,
    clock_in TIMESTAMPTZ,        -- 出勤時刻
    clock_out TIMESTAMPTZ,       -- 退勤時刻
    break_minutes INTEGER DEFAULT 60,   -- 休憩時間（分）
    actual_work_minutes INTEGER GENERATED ALWAYS AS (
        CASE
            WHEN clock_in IS NOT NULL AND clock_out IS NOT NULL
            THEN GREATEST(0, EXTRACT(EPOCH FROM (clock_out - clock_in))::INTEGER / 60 - break_minutes)
            ELSE NULL
        END
    ) STORED,                    -- 実労働時間（分）自動計算
    overtime_minutes INTEGER DEFAULT 0,  -- 残業時間（分）
    status TEXT NOT NULL DEFAULT 'present'
        CHECK (status IN ('present', 'absent', 'paid_leave', 'sick_leave', 'special_leave', 'holiday')),
    note TEXT,
    approved_by UUID REFERENCES users(id),  -- 承認者
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (employee_id, work_date)  -- 1日1レコード制約
);

COMMENT ON TABLE attendance_records IS '日次勤怠記録。1従業員1日1レコード。給与計算パイプラインの入力源。';
COMMENT ON COLUMN attendance_records.actual_work_minutes IS '実労働時間（分）。(clock_out - clock_in) - break_minutes で自動計算（GENERATED ALWAYS AS STORED）。';
COMMENT ON COLUMN attendance_records.overtime_minutes IS '残業時間（分）。actual_work_minutes - scheduled_work_hours * 60 を基に勤怠パイプラインが書き込む。';
COMMENT ON COLUMN attendance_records.status IS '勤怠区分: present/absent/paid_leave/sick_leave/special_leave/holiday';
COMMENT ON COLUMN attendance_records.approved_by IS '月次締め承認者のユーザーID。NULLは未承認。';

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX idx_attendance_company
    ON attendance_records (company_id);

CREATE INDEX idx_attendance_employee
    ON attendance_records (employee_id);

-- 月次集計・36協定チェックで会社×日付の範囲検索が頻繁に発生する
CREATE INDEX idx_attendance_date
    ON attendance_records (company_id, work_date DESC);

-- 特定従業員の履歴参照
CREATE INDEX idx_attendance_employee_date
    ON attendance_records (employee_id, work_date DESC);

-- =============================================================================
-- View: 月次集計（給与計算パイプラインが参照）
-- =============================================================================

CREATE OR REPLACE VIEW attendance_monthly_summary AS
SELECT
    company_id,
    employee_id,
    DATE_TRUNC('month', work_date) AS month,
    COUNT(*) FILTER (WHERE status = 'present')     AS work_days,
    COUNT(*) FILTER (WHERE status = 'paid_leave')  AS paid_leave_used,
    COUNT(*) FILTER (WHERE status = 'absent')      AS absent_days,
    COALESCE(SUM(actual_work_minutes), 0)          AS total_work_minutes,
    COALESCE(SUM(overtime_minutes), 0)             AS total_overtime_minutes
FROM attendance_records
GROUP BY company_id, employee_id, DATE_TRUNC('month', work_date);

COMMENT ON VIEW attendance_monthly_summary IS
    '勤怠月次集計ビュー。給与計算パイプライン（payroll_pipeline.py）が月次締め時に参照する。';

-- =============================================================================
-- RLS（Row Level Security）
-- =============================================================================

ALTER TABLE attendance_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY "attendance_records_tenant_isolation" ON attendance_records
    USING (company_id = (current_setting('app.company_id', true))::UUID);

-- =============================================================================
-- Trigger: updated_at 自動更新
-- update_updated_at() 関数は 001_initial_schema.sql で定義済み
-- =============================================================================

CREATE TRIGGER trg_attendance_records_updated_at
    BEFORE UPDATE ON attendance_records
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

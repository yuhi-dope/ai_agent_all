-- 043_manual_invoices.sql
-- 口座振替・手動請求対応テーブル（建設/製造の中小企業向け）
-- payment_method: bank_transfer | invoice

CREATE TABLE manual_invoices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    amount_yen      INTEGER NOT NULL CHECK (amount_yen > 0),
    description     TEXT,
    due_date        DATE,
    payment_method  TEXT NOT NULL DEFAULT 'bank_transfer'
                        CHECK (payment_method IN ('bank_transfer', 'invoice')),
    bank_info       JSONB,                          -- 振込先口座情報（任意）
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'paid', 'overdue', 'canceled')),
    paid_at         TIMESTAMPTZ,
    created_by      UUID,                           -- admin user_id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- updated_at 自動更新トリガー
CREATE OR REPLACE FUNCTION update_manual_invoices_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_manual_invoices_updated_at
    BEFORE UPDATE ON manual_invoices
    FOR EACH ROW EXECUTE FUNCTION update_manual_invoices_updated_at();

-- インデックス: 主要クエリパターンに対応
CREATE INDEX idx_manual_invoices_company_status
    ON manual_invoices (company_id, status);

CREATE INDEX idx_manual_invoices_due_date
    ON manual_invoices (due_date)
    WHERE status = 'pending';  -- 期日管理（未払いのみ）

-- RLS 有効化
ALTER TABLE manual_invoices ENABLE ROW LEVEL SECURITY;

-- 自テナントのレコードのみ参照可
CREATE POLICY manual_invoices_select
    ON manual_invoices FOR SELECT
    USING (company_id = (current_setting('app.company_id', true))::UUID);

-- admin ロールのみ INSERT 可
CREATE POLICY manual_invoices_insert
    ON manual_invoices FOR INSERT
    WITH CHECK (
        company_id = (current_setting('app.company_id', true))::UUID
    );

-- admin ロールのみ UPDATE 可（status 変更・paid_at 記録）
CREATE POLICY manual_invoices_update
    ON manual_invoices FOR UPDATE
    USING (company_id = (current_setting('app.company_id', true))::UUID)
    WITH CHECK (company_id = (current_setting('app.company_id', true))::UUID);

COMMENT ON TABLE manual_invoices IS '口座振替・請求書払い対応の手動請求書テーブル。建設/製造業の中小企業向け。';
COMMENT ON COLUMN manual_invoices.payment_method IS 'bank_transfer=口座振替, invoice=請求書払い';
COMMENT ON COLUMN manual_invoices.bank_info IS '振込先口座情報 JSONB（bank_name, branch_name, account_type, account_number, account_name 等）';
COMMENT ON COLUMN manual_invoices.status IS 'pending=未払い, paid=支払済み, overdue=期日超過, canceled=キャンセル';

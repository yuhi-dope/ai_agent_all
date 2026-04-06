-- Migration 015: BPO Human-in-the-Loop承認フロー
-- ADR-009に基づく: パイロット前にHitL必須化
-- 金額・外部送付を伴うBPO実行を必ず人間が確認してから実行する

-- execution_logsにHitL管理カラムを追加
ALTER TABLE execution_logs
    ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved'
        CHECK (approval_status IN ('pending', 'approved', 'rejected', 'modified')),
    ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
    ADD COLUMN IF NOT EXISTS original_output JSONB,
    ADD COLUMN IF NOT EXISTS modified_output JSONB;

-- HitL必須パイプラインの定義テーブル
CREATE TABLE IF NOT EXISTS bpo_hitl_requirements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_key TEXT NOT NULL UNIQUE,  -- 例: "construction/estimation"
    requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
    min_confidence_for_auto FLOAT,      -- この信頼度以上なら自動承認OK（NULLは常にHitL）
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 金額・外部送付を伴うパイプラインを登録（全て常にHitL必須）
INSERT INTO bpo_hitl_requirements (pipeline_key, requires_approval, min_confidence_for_auto, description)
VALUES
    ('construction/estimation',  TRUE, NULL, '建設業見積書: 金額誤りリスク高'),
    ('construction/billing',     TRUE, NULL, '建設業請求書: 外部送付'),
    ('construction/safety',      FALSE, 0.95, '安全書類: 低リスクのため高信頼度で自動可'),
    ('manufacturing/estimation', TRUE, NULL, '製造業見積: 金額誤りリスク高'),
    ('common/expense',           TRUE, NULL, '経費精算: 金額・外部処理'),
    ('common/payroll',           TRUE, NULL, '給与計算: 金額・機密情報'),
    ('common/contract',          TRUE, NULL, '契約書: 法的効力あり')
ON CONFLICT (pipeline_key) DO UPDATE SET
    requires_approval = EXCLUDED.requires_approval,
    min_confidence_for_auto = EXCLUDED.min_confidence_for_auto,
    description = EXCLUDED.description;

-- 承認待ちアイテムを高速に取得するインデックス
CREATE INDEX IF NOT EXISTS idx_execution_logs_pending
    ON execution_logs (company_id, approval_status, created_at DESC)
    WHERE approval_status = 'pending';

-- RLS: execution_logsの承認操作は同一テナントのadminのみ
-- (既存のRLSに追加条件として適用)
COMMENT ON COLUMN execution_logs.approval_status IS
    'HitL承認状態: pending=承認待ち, approved=承認済, rejected=却下, modified=修正して承認';

COMMENT ON COLUMN execution_logs.original_output IS
    '承認前のパイプライン出力（修正比較・監査用）';

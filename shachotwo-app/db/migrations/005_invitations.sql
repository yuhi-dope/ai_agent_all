-- 005_invitations.sql — メンバー招待テーブル
-- 管理者が同じ会社のメンバーを招待するための仕組み

CREATE TABLE invitations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id UUID NOT NULL REFERENCES companies(id),
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'editor' CHECK (role IN ('admin', 'editor')),
    invited_by UUID NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'expired', 'cancelled')),
    accepted_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 同一会社・同一メールの重複招待を防止（pending状態のもの）
CREATE UNIQUE INDEX idx_invitations_unique_pending
    ON invitations (company_id, email)
    WHERE status = 'pending';

-- テナント分離インデックス
CREATE INDEX idx_invitations_company ON invitations (company_id, status);
CREATE INDEX idx_invitations_email ON invitations (email, status);

-- RLS
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
CREATE POLICY "invitations_tenant_isolation" ON invitations
    USING (company_id = (current_setting('app.company_id', true))::UUID);

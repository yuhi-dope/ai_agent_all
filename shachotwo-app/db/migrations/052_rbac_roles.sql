-- RBAC 5ロール定義
-- admin: 全権限（招待・承認・設定変更）
-- approver: 承認のみ（起票不可）
-- editor: データ入力・パイプライン実行（承認不可）
-- viewer: 閲覧のみ（変更不可）
-- auditor: 監査ログ閲覧専用（他の操作不可）

-- users テーブルの role カラムに新ロールを許容するよう制約を更新
-- 既存の 'admin'/'editor' は維持（後方互換）
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('admin', 'approver', 'editor', 'viewer', 'auditor'));

-- invitations テーブルも同様に更新
ALTER TABLE invitations DROP CONSTRAINT IF EXISTS invitations_role_check;
ALTER TABLE invitations ADD CONSTRAINT invitations_role_check
    CHECK (role IN ('admin', 'approver', 'editor', 'viewer', 'auditor'));

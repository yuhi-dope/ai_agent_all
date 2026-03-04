-- 008: ロール体系変更 — owner → admin、owner は開発側（DEVELOPER_EMAILS）専用に
--
-- 変更前: 会社作成者 = owner、参加者 = member
-- 変更後: 会社作成者 = admin、参加者 = member、owner = 開発側（コード側で付与）
--
-- 既存の owner ロールを admin に一括変更する

UPDATE user_companies
SET role = 'admin'
WHERE role = 'owner';

-- role カラムにコメント追加
COMMENT ON COLUMN user_companies.role IS 'owner=プラットフォーム運営者, admin=企業管理者, member=一般メンバー';

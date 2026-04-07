-- companies テーブルに allowed_domains カラムを追加
-- 招待時にメールアドレスのドメインがこのリストに含まれているか検証する
--
-- 設計:
--   allowed_domains TEXT[] — 許可するメールドメイン（例: {"minato.co.jp", "minato.com"}）
--   空配列 or NULL の場合は制限なし（初期状態・移行期のフォールバック）
--
-- 使用例:
--   UPDATE companies SET allowed_domains = ARRAY['minato.co.jp'] WHERE id = '...';

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS allowed_domains TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN companies.allowed_domains IS
    '招待メールの許可ドメイン一覧。空配列の場合は制限なし（オープン招待）。例: {minato.co.jp, minato.com}';

-- ドメイン検索用GINインデックス（ANY検索の高速化）
CREATE INDEX IF NOT EXISTS idx_companies_allowed_domains
    ON companies USING GIN (allowed_domains);

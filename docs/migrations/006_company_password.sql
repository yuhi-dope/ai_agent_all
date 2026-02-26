-- 006: companies テーブルにパスワードハッシュカラムを追加
-- 会社レベルのパスワード認証（ログイン）に使用

ALTER TABLE companies ADD COLUMN IF NOT EXISTS password_hash TEXT;

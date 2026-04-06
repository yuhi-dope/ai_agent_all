-- 010: knowledge_sessions にファイルメタデータカラム追加
-- ファイルアップロード時に元ファイルを Supabase Storage に保存し、
-- ナレッジ詳細画面でソースファイルの閲覧・削除・再アップロードを可能にする

ALTER TABLE knowledge_sessions
  ADD COLUMN IF NOT EXISTS file_name TEXT,
  ADD COLUMN IF NOT EXISTS file_size INTEGER,
  ADD COLUMN IF NOT EXISTS file_content_type TEXT,
  ADD COLUMN IF NOT EXISTS file_storage_path TEXT;

COMMENT ON COLUMN knowledge_sessions.file_name IS '元のファイル名';
COMMENT ON COLUMN knowledge_sessions.file_size IS 'ファイルサイズ(bytes)';
COMMENT ON COLUMN knowledge_sessions.file_content_type IS 'MIMEタイプ';
COMMENT ON COLUMN knowledge_sessions.file_storage_path IS 'Supabase Storage 内のパス (bucket: knowledge-files)';

-- Storage bucket はSupabase Dashboard or supabase CLI で作成:
-- supabase storage create knowledge-files --public=false

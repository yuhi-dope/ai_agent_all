-- 015: saas_context カラムを saas_tasks テーブルに追加
-- 計画時にプレフェッチしたSaaSスキーマ情報（フィールド定義・レイアウト・ビュー設定）を保存する
-- ユーザーが承認前に現在のアプリ状態を確認できるようにする

ALTER TABLE saas_tasks ADD COLUMN IF NOT EXISTS saas_context TEXT;

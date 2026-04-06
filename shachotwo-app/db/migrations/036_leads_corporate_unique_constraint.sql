-- leads: upsert用のUNIQUE制約を追加（Supabase upsert on_conflict対応）
-- 部分インデックス(031)はそのまま残す（NULLガード）

-- まず既存の重複を除去（最新のレコードを残す）
DELETE FROM leads a
USING leads b
WHERE a.company_id = b.company_id
  AND a.corporate_number = b.corporate_number
  AND a.corporate_number IS NOT NULL
  AND btrim(a.corporate_number) <> ''
  AND a.created_at < b.created_at;

-- corporate_number が空/NULLのレコードにダミー値を設定しない
-- → UNIQUE制約はcorporate_numberがNOT NULLの行にのみ適用
ALTER TABLE leads ADD CONSTRAINT leads_company_corporate_uq
  UNIQUE (company_id, corporate_number);

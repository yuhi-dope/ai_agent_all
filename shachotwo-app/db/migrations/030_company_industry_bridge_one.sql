-- データ修正: 株式会社ブリッジワン（テナント）の業種を建設→製造に変更
-- company_id: 86ea5be1-6121-4303-b8d4-84c26b7906b6
-- 本番以外では該当行が無い場合は 0 件更新で問題なし。
-- ナレッジテンプレの差し替えは行わない（必要なら別途 apply-template を実行）。

UPDATE companies
SET industry = 'manufacturing'
WHERE id = '86ea5be1-6121-4303-b8d4-84c26b7906b6'::uuid
  AND industry = 'construction';

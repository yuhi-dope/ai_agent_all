-- 009: 既存テスト企業データを削除してやり直し
-- 対象: company_id = adfaa624-30d4-44ee-b17f-8ce09f0a9e30
-- REQUIRE_AUTH=true に切り替える前にクリーンアップする

-- 招待トークン削除
DELETE FROM invite_tokens
WHERE company_id = 'adfaa624-30d4-44ee-b17f-8ce09f0a9e30';

-- ユーザー紐付け削除
DELETE FROM user_companies
WHERE company_id = 'adfaa624-30d4-44ee-b17f-8ce09f0a9e30';

-- 会社本体削除
DELETE FROM companies
WHERE id = 'adfaa624-30d4-44ee-b17f-8ce09f0a9e30';

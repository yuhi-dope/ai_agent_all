-- 004: 企業プロフィール情報（従業員数・売上・業種）+ オンボーディング
-- business_plan.md の「年商10億〜100億円の SMB」ターゲットに必要な情報

-- ============================================================
-- companies テーブルに企業プロフィールカラム追加
-- ============================================================
ALTER TABLE companies ADD COLUMN IF NOT EXISTS employee_count TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS annual_revenue TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS industry TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS onboarding JSONB DEFAULT '{}';

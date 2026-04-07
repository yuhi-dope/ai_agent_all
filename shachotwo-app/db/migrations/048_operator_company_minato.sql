-- 運営会社情報の設定: 湊合同会社（シャチョツーの運営主体）
-- 愛知県北名古屋市に登記所在地
-- 用途: 契約書テンプレート / 利用規約 / 特商法表記 等の乙（提供者）情報

-- app_settings テーブルが存在する場合は運営会社情報を upsert
-- （テーブルが存在しない場合は後続の048b等でテーブル作成後に適用する）

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'app_settings'
  ) THEN
    INSERT INTO app_settings (key, value)
    VALUES
      ('operator_company_name',     '湊合同会社'),
      ('operator_corporate_number', '1180003027798'),
      ('operator_representative',   '杉本 雄飛'),
      ('operator_address',          '愛知県北名古屋市熊之庄新宮197番地3'),
      ('operator_postal_code',      '481-0006'),
      ('operator_service_name',     'シャチョツー')
    ON CONFLICT (key) DO UPDATE
      SET value = EXCLUDED.value,
          updated_at = NOW();
  END IF;
END $$;

-- companies テーブル上の運営会社テナントを更新（存在する場合のみ）
-- company_id は本番環境の実際の値に置き換えること
-- UPDATE companies
-- SET
--   name           = '湊合同会社',
--   address        = '愛知県北名古屋市',
--   representative = '杉本 祐陽'
-- WHERE id = '<operator-company-uuid>'::uuid;

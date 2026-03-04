-- ============================================================
-- デモデータと指定ユーザー関連データの削除スクリプト
-- 実行前必ずバックアップを取得してください。
-- Supabase Dashboard > SQL Editor で実行するか、psql で実行してください。
--
-- 【Auth ユーザー削除】このSQLでは auth.users は触りません。
-- sugimoto_yuhi@bridge-one.co.jp を完全に消すには、実行後に以下を実施してください。
-- 1. Supabase Dashboard > Authentication > Users を開く
-- 2. sugimoto_yuhi@bridge-one.co.jp の行を選択
-- 3. 右側またはメニューから「Delete user」で削除
-- ============================================================

-- 1) sugimoto_yuhi@bridge-one.co.jp の user_id (Auth Users の UID)
-- このユーザーをアプリ側のテーブルから削除します。
-- ※ Auth のユーザー自体はダッシュボードの Authentication > Users から手動で削除してください。
DO $$
DECLARE
  target_user_id UUID := '4fb943df-b444-45d8-977c-a59972f67d43';
BEGIN
  DELETE FROM user_companies WHERE user_id = target_user_id;
  RAISE NOTICE 'user_companies: deleted rows for user %', target_user_id;
END $$;

-- 2) デモ用 run_id / company_id パターンで削除（スキーマにあるテーブル）
-- run_id が 'demo-%' のレコード
DELETE FROM audit_logs   WHERE run_id LIKE 'demo-%';
DELETE FROM rule_changes WHERE run_id LIKE 'demo-%';
DELETE FROM features     WHERE run_id LIKE 'demo-%';
DELETE FROM runs        WHERE run_id LIKE 'demo-%';

-- 3) channel_configs（company_id が TEXT のため demo_company 等を直接指定）
DELETE FROM channel_configs
WHERE company_id IN ('demo_company', 'demo_company_b');

-- 3b) oauth_tokens（tenant_id が TEXT で企業IDを保持。デモ用 tenant_id を削除）
DELETE FROM oauth_tokens
WHERE tenant_id IN ('demo_company', 'demo_company_b');

-- 4) 存在する場合のみ削除するテーブル（ダッシュボードで作成したテーブル等）
-- app_connections
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'app_connections') THEN
    DELETE FROM app_connections
    WHERE id::text LIKE 'demo-%' OR company_id::text IN ('demo_company', 'demo_company_b');
    RAISE NOTICE 'app_connections: demo rows deleted';
  END IF;
END $$;

-- implementation_candidates
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'implementation_candidates') THEN
    DELETE FROM implementation_candidates
    WHERE id::text LIKE 'demo-%' OR company_id::text IN ('demo_company', 'demo_company_b');
    RAISE NOTICE 'implementation_candidates: demo rows deleted';
  END IF;
END $$;

-- ingestion_warnings
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'ingestion_warnings') THEN
    DELETE FROM ingestion_warnings
    WHERE id::text LIKE 'demo-%' OR run_id LIKE 'demo-%' OR company_id::text IN ('demo_company', 'demo_company_b');
    RAISE NOTICE 'ingestion_warnings: demo rows deleted';
  END IF;
END $$;

-- rule_candidates
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rule_candidates') THEN
    DELETE FROM rule_candidates
    WHERE id::text LIKE 'demo-%' OR run_id LIKE 'demo-%' OR company_id::text IN ('demo_company', 'demo_company_b');
    RAISE NOTICE 'rule_candidates: demo rows deleted';
  END IF;
END $$;

-- operation_alerts（デモ用 id / run_id がある場合）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'operation_alerts') THEN
    EXECUTE 'DELETE FROM operation_alerts WHERE id::text LIKE ''demo-%''';
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'operation_alerts' AND column_name = 'run_id') THEN
      EXECUTE 'DELETE FROM operation_alerts WHERE run_id LIKE ''demo-%''';
    END IF;
    RAISE NOTICE 'operation_alerts: demo rows deleted';
  END IF;
END $$;

-- ============================================================
-- 5) 株式会社ブリッジワン の企業データを削除
--    companies を参照する子テーブルから順に削除し、最後に companies を削除します。
--    company_saas_connections / saas_tasks は ON DELETE CASCADE のため companies 削除で連鎖削除されます。
-- ============================================================
DO $$
DECLARE
  bridge_one_ids UUID[];
BEGIN
  SELECT ARRAY_AGG(id) INTO bridge_one_ids
  FROM companies WHERE name = '株式会社ブリッジワン';

  IF bridge_one_ids IS NULL OR array_length(bridge_one_ids, 1) IS NULL THEN
    RAISE NOTICE 'companies: no rows with name 株式会社ブリッジワン';
    RETURN;
  END IF;

  DELETE FROM user_companies   WHERE company_id = ANY(bridge_one_ids);
  DELETE FROM invite_tokens    WHERE company_id = ANY(bridge_one_ids);
  DELETE FROM audit_logs       WHERE company_id = ANY(bridge_one_ids);
  DELETE FROM runs             WHERE company_id = ANY(bridge_one_ids);
  DELETE FROM features         WHERE company_id = ANY(bridge_one_ids);

  -- oauth_tokens（tenant_id が TEXT で企業UUIDを保持）
  DELETE FROM oauth_tokens
  WHERE tenant_id = ANY(ARRAY(SELECT unnest(bridge_one_ids)::text));

  -- channel_configs は company_id が TEXT（slug または id の文字列の可能性）
  DELETE FROM channel_configs
  WHERE company_id IN (SELECT id::text FROM companies WHERE name = '株式会社ブリッジワン')
     OR company_id IN (SELECT slug FROM companies WHERE name = '株式会社ブリッジワン');

  -- 存在する場合のみ: company_profiles, company_profile_snapshots, company_app_urls
  -- （company_id が TEXT のテーブルは ::uuid で比較）
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'company_profiles') THEN
    EXECUTE 'DELETE FROM company_profiles WHERE company_id::uuid = ANY($1)' USING bridge_one_ids;
    RAISE NOTICE 'company_profiles: bridge-one rows deleted';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'company_profile_snapshots') THEN
    EXECUTE 'DELETE FROM company_profile_snapshots WHERE company_id::uuid = ANY($1)' USING bridge_one_ids;
    RAISE NOTICE 'company_profile_snapshots: bridge-one rows deleted';
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'company_app_urls') THEN
    EXECUTE 'DELETE FROM company_app_urls WHERE company_id::uuid = ANY($1)' USING bridge_one_ids;
    RAISE NOTICE 'company_app_urls: bridge-one rows deleted';
  END IF;

  DELETE FROM companies WHERE name = '株式会社ブリッジワン';
  RAISE NOTICE 'companies: 株式会社ブリッジワン deleted';
END $$;

-- 完了
SELECT 'Cleanup finished. Delete Auth user sugimoto_yuhi@bridge-one.co.jp from Dashboard > Authentication > Users.' AS next_step;

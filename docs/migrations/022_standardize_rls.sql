-- 022: RLS ポリシーを tenant_isolation に統一
-- 全許可 (USING true) → テナント分離 (current_setting('app.company_id')) に変更
-- 対象: company_saas_connections(011), saas_tasks(013), operation_patterns(014),
--        saas_schema_snapshots(014), task_corrections(020)
-- ※ saas_structure_knowledge(018), bpo_specialist_maturity(019) は既に tenant_isolation

-- ============================================================
-- company_saas_connections: csc_service_all → tenant_isolation
-- ============================================================
DROP POLICY IF EXISTS csc_service_all ON company_saas_connections;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'company_saas_connections' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON company_saas_connections
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- saas_tasks: saas_tasks_service_all → tenant_isolation
-- ============================================================
DROP POLICY IF EXISTS saas_tasks_service_all ON saas_tasks;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'saas_tasks' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON saas_tasks
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- operation_patterns: op_patterns_service_all → tenant_isolation
-- ============================================================
DROP POLICY IF EXISTS op_patterns_service_all ON operation_patterns;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'operation_patterns' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON operation_patterns
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- saas_schema_snapshots: schema_snap_service_all → tenant_isolation
-- ============================================================
DROP POLICY IF EXISTS schema_snap_service_all ON saas_schema_snapshots;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'saas_schema_snapshots' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON saas_schema_snapshots
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

-- ============================================================
-- task_corrections: task_corrections_service_all → tenant_isolation
-- ============================================================
DROP POLICY IF EXISTS task_corrections_service_all ON task_corrections;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'task_corrections' AND policyname = 'tenant_isolation'
    ) THEN
        CREATE POLICY tenant_isolation ON task_corrections
            FOR ALL USING (company_id = current_setting('app.company_id', true)::UUID);
    END IF;
END $$;

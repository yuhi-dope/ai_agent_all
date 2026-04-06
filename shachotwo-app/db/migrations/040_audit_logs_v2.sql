-- ============================================================
-- 040_audit_logs_v2.sql
-- SOC2準備: audit_logsテーブルの完全化
-- 既存テーブルがあればALTER TABLE、なければCREATE
-- ============================================================

-- 既存のaudit_logsテーブルを拡張（存在しない場合はCREATEする）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'audit_logs'
  ) THEN
    -- テーブルが存在しない場合: 新規作成
    CREATE TABLE public.audit_logs (
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      company_id      UUID NOT NULL,
      actor_user_id   UUID,
      actor_role      TEXT,
      action          TEXT NOT NULL,
      resource_type   TEXT NOT NULL,
      resource_id     TEXT,
      old_values      JSONB,
      new_values      JSONB,
      ip_address      INET,
      user_agent      TEXT,
      metadata        JSONB,
      created_at      TIMESTAMPTZ DEFAULT now() NOT NULL
    );
  ELSE
    -- テーブルが存在する場合: 不足カラムを追加
    -- actor_role カラム
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'actor_role'
    ) THEN
      ALTER TABLE public.audit_logs ADD COLUMN actor_role TEXT;
    END IF;

    -- actor_user_id カラム（旧名: user_id の場合リネーム）
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'actor_user_id'
    ) THEN
      -- user_id が存在すればリネーム、なければ追加
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'user_id'
      ) THEN
        ALTER TABLE public.audit_logs RENAME COLUMN user_id TO actor_user_id;
      ELSE
        ALTER TABLE public.audit_logs ADD COLUMN actor_user_id UUID;
      END IF;
    END IF;

    -- old_values カラム
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'old_values'
    ) THEN
      ALTER TABLE public.audit_logs ADD COLUMN old_values JSONB;
    END IF;

    -- new_values カラム（旧名: details の場合は別途保持しつつ追加）
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'new_values'
    ) THEN
      ALTER TABLE public.audit_logs ADD COLUMN new_values JSONB;
    END IF;

    -- user_agent カラム
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'user_agent'
    ) THEN
      ALTER TABLE public.audit_logs ADD COLUMN user_agent TEXT;
    END IF;

    -- metadata カラム
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'metadata'
    ) THEN
      ALTER TABLE public.audit_logs ADD COLUMN metadata JSONB;
    END IF;

    -- ip_address を INET 型に変換（TEXT だった場合）
    -- ※ 型変換は安全でないため、互換性維持のためコメントアウト
    -- ALTER TABLE public.audit_logs ALTER COLUMN ip_address TYPE INET USING ip_address::INET;

    -- resource_type カラム（旧名: target_type の場合リネーム）
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'resource_type'
    ) THEN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'target_type'
      ) THEN
        ALTER TABLE public.audit_logs RENAME COLUMN target_type TO resource_type;
      ELSE
        ALTER TABLE public.audit_logs ADD COLUMN resource_type TEXT NOT NULL DEFAULT 'unknown';
      END IF;
    END IF;

    -- resource_id カラム（旧名: target_id の場合リネーム）
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'resource_id'
    ) THEN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs' AND column_name = 'target_id'
      ) THEN
        ALTER TABLE public.audit_logs RENAME COLUMN target_id TO resource_id;
      ELSE
        ALTER TABLE public.audit_logs ADD COLUMN resource_id TEXT;
      END IF;
    END IF;
  END IF;
END;
$$;

-- ============================================================
-- インデックス（既存がなければ作成）
-- ============================================================

-- company_id + created_at: テナント別タイムライン検索
CREATE INDEX IF NOT EXISTS idx_audit_logs_company_created
  ON public.audit_logs (company_id, created_at DESC);

-- actor_user_id: ユーザー別操作履歴検索
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_user
  ON public.audit_logs (actor_user_id)
  WHERE actor_user_id IS NOT NULL;

-- action: 操作種別フィルタリング
CREATE INDEX IF NOT EXISTS idx_audit_logs_action
  ON public.audit_logs (action);

-- resource_type: リソース種別フィルタリング
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource_type
  ON public.audit_logs (resource_type);

-- ============================================================
-- RLS（Row Level Security）
-- SOC2要件: SELECTはadminのみ、INSERTはservice roleのみ
-- ============================================================

ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

-- 既存ポリシーを削除して再作成
DROP POLICY IF EXISTS audit_logs_select_admin ON public.audit_logs;
DROP POLICY IF EXISTS audit_logs_insert_service ON public.audit_logs;

-- adminのみ自社のログを参照可能
CREATE POLICY audit_logs_select_admin ON public.audit_logs
  FOR SELECT
  USING (
    company_id = (
      SELECT company_id FROM public.users
      WHERE id = auth.uid()
    )
    AND (
      SELECT role FROM public.users
      WHERE id = auth.uid()
    ) = 'admin'
  );

-- INSERT は service role のみ（RLSをbypassするservice_roleキー経由でのみ挿入可能）
-- service_roleはRLSをbypassするため、USING句は通常ユーザー（anon/authenticated）への制限
CREATE POLICY audit_logs_insert_service ON public.audit_logs
  FOR INSERT
  WITH CHECK (false);
-- ↑ authenticatedユーザーからの直接INSERTを禁止
-- service_role（バックエンド）はRLS bypassのため影響なし

COMMENT ON TABLE public.audit_logs IS
  'SOC2準拠監査ログ。全CRUD操作・ログイン・エクスポートを記録。5年間保持。INSERT専用（UPDATE/DELETE禁止）。';
COMMENT ON COLUMN public.audit_logs.actor_role IS 'admin / editor';
COMMENT ON COLUMN public.audit_logs.action IS 'create / read / update / delete / login / logout / export / approve / reject';
COMMENT ON COLUMN public.audit_logs.resource_type IS 'pipeline / knowledge_item / execution_log / billing / user / connector';
COMMENT ON COLUMN public.audit_logs.old_values IS '変更前の値（update/delete時に設定）';
COMMENT ON COLUMN public.audit_logs.new_values IS '変更後の値（create/update時に設定）';

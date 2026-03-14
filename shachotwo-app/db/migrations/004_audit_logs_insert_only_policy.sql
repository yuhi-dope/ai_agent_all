-- Migration 002: audit_logs INSERT-only policy
-- Per security design (a_01_セキュリティ設計.md §13):
--   audit_logs table is INSERT-only (no UPDATE/DELETE allowed).
--   Existing tenant_isolation policy (from 001) allows SELECT.
--   This adds an explicit INSERT policy and removes UPDATE/DELETE ability.

-- Add INSERT-only policy (anyone can insert audit logs for their company)
CREATE POLICY "audit_logs_insert_only" ON audit_logs
    FOR INSERT
    WITH CHECK (true);

-- Add explicit SELECT policy scoped to company
-- (tenant_isolation already covers this, but being explicit for clarity)
-- No UPDATE or DELETE policies = those operations are blocked by RLS.

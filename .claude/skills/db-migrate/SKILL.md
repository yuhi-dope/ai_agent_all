---
name: db-migrate
description: DBマイグレーションSQLを作成する。テーブル追加・カラム変更・RPC関数作成・スキーマ変更時に使用。
argument-hint: "[description] (例: add_whatif_simulations_table, add_embedding_backfill_function)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

## DBマイグレーション作成

内容: $ARGUMENTS

### 手順

1. **既存スキーマ確認**: `db/schema.sql` と `db/migrations/` の既存マイグレーションを読む
2. **設計確認**: `shachotwo/c_事業計画/c_02_プロダクト設計.md` のDB設計セクションを参照
3. **マイグレーション作成**: `db/migrations/NNN_<description>.sql` に連番で作成

### ルール
- 既存テーブルの変更は `ALTER TABLE` で追加のみ（既存カラム変更は原則禁止）
- 全テーブルに `company_id` ベースのRLS必須
- RLSポリシーの変更は `DROP POLICY IF EXISTS` → `CREATE POLICY` の順序
- インデックスは `CREATE INDEX IF NOT EXISTS` で冪等にする
- `CREATE OR REPLACE FUNCTION` でRPC関数を作成
- コメントで用途を明記

### 既存マイグレーション
!`ls shachotwo-app/db/migrations/`

# スタック・ドメイン・自社前提

## スタック

- 新規開発は **Next.js (App Router)** + **Supabase** を前提とする。
- API は Route Handlers または Server Actions を使用する。
- DB は Supabase (PostgreSQL)。認証は Supabase Auth を優先する。

## ドメイン

- 主対象は **CRM/SFA**・**事務**・**会計**・**法務**。
- 用語の標準: 取引先、商談、案件、請求、勘定科目、契約、稟議 等。必要に応じて自社用語を追記する。

## Supabase RLS 標準パターン（全テーブル共通）

新規テーブルを作成する際は**必ず以下の RLS を設定すること**。RLS なしのテーブルは全社データが漏洩するリスクがあるため必須。

```sql
-- RLS を有効化（必須）
ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;

-- 読み取り: 自社データのみ
CREATE POLICY "{table_name}_select" ON {table_name}
  FOR SELECT USING (company_id = current_setting('app.company_id', true));

-- 挿入: 自社データのみ
CREATE POLICY "{table_name}_insert" ON {table_name}
  FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true));

-- 更新: 自社データのみ
CREATE POLICY "{table_name}_update" ON {table_name}
  FOR UPDATE USING (company_id = current_setting('app.company_id', true));

-- 削除（必要な場合のみ）
CREATE POLICY "{table_name}_delete" ON {table_name}
  FOR DELETE USING (company_id = current_setting('app.company_id', true));
```

**Coder への指示**:
- 生成するすべての `CREATE TABLE` に対して上記 RLS パターンを適用すること
- `company_id TEXT` カラムが存在しないテーブル（マスタテーブル等）は RLS 対象外としてよいが、設計書にその理由を明記すること
- Supabase Auth での認証チェックとは別レイヤー。API Route で認証していても RLS は別途設定が必要

## 自社前提・テンプレート

- テンプレートは運用の中で「この状態をテンプレートとして認定する」をいつでも実行できる。
- 認定時は、その時点のプロジェクト状態（パス・規約・推奨構成）をこのファイルや別の参照用ファイルに書き出し、spec/coder が参照する。

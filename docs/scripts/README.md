# DB スクリプト

## デモデータ・指定ユーザーの削除

**ファイル:** `cleanup_demo_and_user.sql`

### 削除対象

- **sugimoto_yuhi@bridge-one.co.jp** に紐づく `user_companies` の行
- **デモデータ**: `run_id` が `demo-%` のレコード、`company_id` が `demo_company` / `demo_company_b` のレコード（該当テーブルすべて）
- **株式会社ブリッジワン**: `companies.name = '株式会社ブリッジワン'` の企業と、その企業に紐づく全テーブルのデータ（`user_companies`, `invite_tokens`, `runs`, `features`, `audit_logs`, `channel_configs`, `company_saas_connections`, `saas_tasks` および存在する場合は `company_profiles` 等）

### 手順

1. **（推奨）バックアップ**  
   Supabase Dashboard > Database > Backups でバックアップを取得するか、対象テーブルをエクスポートしてください。

2. **SQL の実行**  
   - Supabase Dashboard を開く  
   - **SQL Editor** を開く  
   - `docs/scripts/cleanup_demo_and_user.sql` の内容を貼り付けて **Run** で実行

3. **Auth ユーザーの削除（sugimoto_yuhi@bridge-one.co.jp）**  
   SQL では `auth.users` は変更しません。認証ユーザーを消すには:  
   - **Authentication** > **Users** を開く  
   - `sugimoto_yuhi@bridge-one.co.jp` の行を選択  
   - **Delete user** で削除

以上で、当該メールのユーザーとデモデータの削除が完了します。

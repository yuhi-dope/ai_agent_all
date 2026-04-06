# 招待制認証への切り替え計画

> **目的**: オープン登録を廃止し、競合によるリサーチ・情報抜き取りを防止する
> **方針**: 1社につき1アカウント（admin）をシャチョツー側が発行 → adminが社内メンバーを招待

## 現状

| 項目 | 現在の状態 |
|---|---|
| 会社登録 | 誰でも `/register` から自由に登録可能 |
| メンバー招待 | admin が招待（`invitations` テーブル + API + UI 実装済み） |
| ロール | admin / editor の2ロール |
| 問題点 | 競合が簡単にアカウント作成→機能・データ構造を調査可能 |

## 変更後のフロー

### 新規顧客オンボーディング（シャチョツー運営側）

```
1. 契約成立
2. 運営が管理画面 or CLIで会社+初期adminアカウントを作成
3. 初期adminにメール送信（パスワード設定リンク付き）
4. adminがログイン → パスワード設定 → ダッシュボードへ
5. adminが設定画面からメンバーを招待（既存フローそのまま）
```

### メンバー追加（既存フロー維持）

```
1. admin が設定 > メンバー管理から招待メール送信
2. 招待されたユーザーがリンクからパスワード設定
3. editor or admin としてログイン
```

## 実装タスク

### Phase A: オープン登録の廃止

- [ ] **A-1** フロントエンド `/register` ページを削除 or アクセス不可にする
  - `/register` → 「このサービスは招待制です」ページに差し替え
  - ログインページから「新規登録」リンクを削除
- [ ] **A-2** バックエンド `POST /api/v1/auth/setup` を制限
  - 招待経由（`app_metadata.company_id` あり）のみ許可
  - 自由登録（company_id なし＝新規会社作成）を拒否
  - または: setupエンドポイント自体を招待専用に分離

### Phase B: 運営プロビジョニング機能

- [ ] **B-1** 管理用APIエンドポイント追加（内部用・認証必須）
  ```
  POST /api/v1/admin/provision-company
  Body: { company_name, industry, admin_email, admin_name }
  ```
  - companies レコード作成
  - Supabase Auth で admin ユーザー作成（invite_user_by_email）
  - users レコード作成（role: admin）
  - app_metadata に company_id + role 設定
  - 招待メール自動送信
- [ ] **B-2** 認証: このエンドポイントは以下のいずれかで保護
  - **Option 1**: 環境変数のAPIキー（`X-Admin-Key` ヘッダー）← MVP推奨
  - **Option 2**: Supabase の service_role_key を持つ内部リクエストのみ
  - **Option 3**: 管理画面UI（Phase 2+）
- [ ] **B-3** CLI スクリプト作成（運営が使う）
  ```bash
  python scripts/provision_company.py \
    --company-name "株式会社XX" \
    --industry "建設業" \
    --admin-email "shacho@example.com" \
    --admin-name "山田太郎"
  ```

### Phase C: ログインフロー調整

- [ ] **C-1** ログインページ修正
  - 「新規登録はこちら」リンク削除
  - 「アカウントをお持ちでない方はお問い合わせください」に変更
- [ ] **C-2** 招待受諾フロー確認
  - 既存の `/invite` ページが正しく動作するか確認
  - パスワード設定 → ログイン → ダッシュボードの遷移テスト
- [ ] **C-3** エラーハンドリング
  - 未招待ユーザーがログインしようとした場合の適切なエラーメッセージ
  - 期限切れ招待の処理

### Phase D: セキュリティ強化（オプション）

- [ ] **D-1** Supabase Auth 設定で "Enable email signup" を無効化
  - これにより Supabase レベルでも自由登録をブロック
  - `invite_user_by_email` のみでユーザー作成可能に
- [ ] **D-2** Rate limiting: ログイン試行回数制限
- [ ] **D-3** 監査ログ: プロビジョニング操作を audit_logs に記録

## 影響範囲

| ファイル | 変更内容 |
|---|---|
| `frontend/src/app/register/page.tsx` | 削除 or 招待制案内ページに差し替え |
| `frontend/src/app/login/page.tsx` | 新規登録リンク削除、案内文変更 |
| `frontend/src/hooks/use-auth.ts` | signUp 関数の除去 or 招待専用に変更 |
| `routers/auth_setup.py` | 招待経由のみ許可するガード追加 |
| `routers/admin.py` | **新規**: プロビジョニングAPI |
| `scripts/provision_company.py` | **新規**: CLIプロビジョニングスクリプト |
| Supabase Dashboard | email signup 無効化設定 |

## 実装順序

```
Phase A（30分）→ Phase B（1時間）→ Phase C（30分）→ Phase D（30分）
合計: 約2.5時間
```

1. **まずPhase A**: 登録を閉じる（即座に競合リスク排除）
2. **次にPhase B**: 運営がアカウントを発行できるようにする
3. **Phase C**: UX調整
4. **Phase D**: 追加セキュリティ

## 注意点

- 既存の招待機能（`invitations` テーブル、`routers/invitations.py`、メンバー管理UI）はそのまま活用
- Supabase の `invite_user_by_email` を活用すれば、メール送信もSupabase任せでOK
- 管理APIのAPIキーは `.env` で管理、絶対にコミットしない

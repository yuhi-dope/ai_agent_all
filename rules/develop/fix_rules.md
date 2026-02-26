# 確認項目・よくあるエラーと対処法

## エラー分析ステップ

1. エラーメッセージを読む（何が起きたか）
2. スタックトレースを確認する（node_modules 以外のどのファイル・行か）
3. エラーをカテゴリ分類する（下記テーブル参照）
4. 最小限の修正を特定する（変更量を増やさない）

## エラーカテゴリ別対処法

### Module / Import エラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `Module not found: Can't resolve '@/...'` | パスエイリアス未設定 or typo | tsconfig の `paths` 確認、パスの typo を修正 |
| `Cannot find module '../xxx'` | 相対パスのミス | ファイル構造を確認し `../` の数を修正 |
| `Property 'xxx' does not exist on type 'never'` | import 先の型が解決できていない | 型定義ファイルの存在を確認、`@types/xxx` を追加 |

### TypeScript 型エラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `Type 'xxx' is not assignable to type 'yyy'` | 型の不一致 | 型定義を修正するか型ガードを追加 |
| `Property 'xxx' does not exist on type 'yyy'` | インターフェースにプロパティがない | interface に該当プロパティを追加 |
| `Object is possibly 'null'` | null チェックがない | `?.` または `if (!x) return` で早期リターン |
| `Argument of type 'xxx' is not assignable` | 関数の引数型ミス | 引数の型を合わせるか関数の型定義を修正 |

### Lint エラー（ruff / ESLint）

| エラー | 原因 | 対処 |
|-------|------|------|
| `F401 'xxx' imported but unused` | 未使用 import | 該当 import を削除 |
| `E501 line too long` | 1 行が長すぎる | 変数に分割または改行 |
| `no-unused-vars` | 未使用変数 | 変数を削除するか `_` プレフィックスを付ける |
| `react-hooks/exhaustive-deps` | useEffect の依存配列不足 | 使用している変数をすべて deps 配列に追加 |

### Next.js / React エラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `Hydration failed` | サーバー/クライアントの HTML が異なる | `Date.now()` や `Math.random()` を useEffect 内に移動 |
| `useRouter is not a function` | App Router と Pages Router の混在 | `next/navigation` の useRouter を使う |
| `NEXT_REDIRECT` in try/catch | redirect() を try 内で呼んでいる | redirect() を try/catch の外に出す |
| `'use client' missing` | クライアントフック（useState等）をサーバーで使用 | ファイル先頭に `'use client'` を追加 |

### Supabase エラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `PGRST116` (0 rows returned) | `.single()` でデータなし | `.maybeSingle()` に変更し null を処理 |
| `JWT expired` | セッション切れ | `supabase.auth.getSession()` で再取得 |
| `permission denied for table xxx` | RLS ポリシーが未設定 or 条件ミス | RLS ポリシーと `company_id` の設定を確認 |
| `duplicate key value violates unique constraint` | 同一キーで INSERT | INSERT の前に既存チェックを追加、または UPSERT を使う |

### Build エラー

| エラー | 原因 | 対処 |
|-------|------|------|
| `Failed to compile` | TypeScript / ESLint エラー | エラーメッセージのファイル・行番号を修正 |
| `Export encountered error` | 動的なコードを静的ページで使用 | `export const dynamic = 'force-dynamic'` を追加 |
| `Cannot use import statement` | CommonJS / ESM の混在 | import/require の形式を統一 |

## 修正指示フォーマット

Fix Agent はエラーを分析した後、以下のフォーマットで Coder への修正指示を出力すること:

```
## 根本原因
- エラーの種類: [型エラー / Lint / ビルド / テスト / セキュリティ]
- 発生箇所: [ファイル名:行番号]
- 原因: [1〜2 文]

## 修正手順
1. [具体的な修正内容]
2. [具体的な修正内容]

## 再発防止
- [次回同様のエラーを防ぐためのルール案]
```

---

（プロジェクト固有のエラーパターンはここに追記する）

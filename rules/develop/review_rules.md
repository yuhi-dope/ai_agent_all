# レビューハウスルール

## チェック順序（Fail Fast）

1. **Secret Scan**: シークレット・API キー・高エントロピー文字列を検出する。検出時は即座に reject。
2. **Lint/Build**: Python の場合は ruff、Node の場合は npm run build で品質を確認する。
3. **Unit Test**: pytest または npm test。未定義の場合はスキップ（合格扱い）。
4. **E2E Test**: Playwright。未導入の場合はスキップ。Unit 合格時のみ実行。
5. **変更量**: 生成コードの合計行数が 200 行以内であること。

## AI セマンティックレビュー（機械チェック合格後に実施）

Lint/Build/Unit/E2E/変更量がすべて合格した場合のみ、以下の観点でコードをレビューし、重大な問題があれば `error_logs` に `[AI Review] ` プレフィックスを付けて追加して `review_ng` を返すこと。

**重要**: 軽微なスタイル・好み・将来の拡張性への懸念は `review_ng` にしない。実際にバグや本番障害につながる問題のみ指摘すること。

### reject する条件

**バグ・論理エラー**
- Off-by-one エラー（配列の範囲外アクセス、ループの境界値ミス）
- null / undefined 参照でクラッシュが起きる可能性（null チェックなしでプロパティアクセス）
- await 忘れ（非同期関数の結果をそのまま使っている）
- 条件式の明らかな誤り（`&&` と `||` の取り違え、比較演算子の誤り）

**セキュリティ**
- SQL インジェクション（文字列結合でクエリを構築している）
- XSS（ユーザー入力を `dangerouslySetInnerHTML` に渡している）
- 認証・認可チェックの欠落（認証なしで保護すべきリソースへアクセス可能）
- ログに個人情報・機密情報を出力している

**パフォーマンス（重大なもののみ）**
- N+1 クエリ（ループ内で DB 呼び出しをしている）
- 全件取得してからアプリ側でフィルタリングしている（大量データで問題になる）

### 判断基準（具体例付き）

#### N+1 クエリ

| パターン | 判定 |
|---------|------|
| `for (const item of items) { await supabase.from(...).select() }` | **reject** |
| `items.map(item => fetch(url + item.id))` | **reject** |
| `await Promise.all(items.map(item => fetch(...)))` | **OK** |
| `.select('*, relations(*)')` で JOIN して一括取得 | **OK** |
| `supabase.from(...).in('id', ids)` で一括取得 | **OK** |

#### null / undefined クラッシュ

| パターン | 判定 |
|---------|------|
| `data.items.map(...)` で `data` が `null` の可能性あり | **reject** |
| `user.name` で `user` が `undefined` になりうる | **reject** |
| `data?.items?.map(...)` | **OK** |
| `if (!data) return null` の早期リターンあり | **OK** |
| `data ?? []` で fallback あり | **OK** |

#### SQL インジェクション

| パターン | 判定 |
|---------|------|
| `` `WHERE name = '${userInput}'` `` を文字列結合 | **reject** |
| `.eq('name', userInput)` や Supabase の API メソッド | **OK** |
| Prisma の `where: { name: userInput }` | **OK** |

#### await 忘れ

| パターン | 判定 |
|---------|------|
| `const data = supabase.from(...).select()` (await なし) | **reject** |
| `const { data } = await supabase.from(...).select()` | **OK** |

#### XSS

| パターン | 判定 |
|---------|------|
| `<div dangerouslySetInnerHTML={{ __html: userInput }}` (未サニタイズ) | **reject** |
| `<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(input) }}` | **OK** |
| `<p>{userInput}</p>` | **OK**（React は自動エスケープ）|

### reject しない条件（改善案として記録するのみ）

- 命名規則・コメント・可読性の問題
- DRY 原則違反（重複コード）
- 将来的な拡張性・保守性の懸念
- パフォーマンスの軽微な改善余地（重大でないもの）

### error_logs フォーマット

```
[AI Review] バグ: customers.tsx:45 - `user` が null の場合に `user.name` でクラッシュする。null チェックを追加すること。
[AI Review] セキュリティ: api/route.ts:23 - クエリパラメータを直接 SQL に結合している。Supabase の `.eq()` メソッドを使うこと。
```

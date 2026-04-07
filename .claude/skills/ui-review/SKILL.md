---
name: ui-review
description: UI_RULES.mdに基づいてフロントエンドページをレビューし、違反を修正する。UIチェック・デザインレビュー・フロント品質確認時に使用。ページ実装後に必ず実行する。
argument-hint: "[page-path | --visual | --visual page-path] (省略時は全ページ対象)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

# UIレビュー

対象: $ARGUMENTS（省略時は全認証済みページ）

## 実行手順

### 1. UI_RULES.md を読む

`shachotwo-app/frontend/UI_RULES.md` を読んでルール全項目を把握する。

### 2. 対象ファイルを特定

$ARGUMENTS が指定された場合: そのファイルのみ
未指定の場合: 以下を対象
```
shachotwo-app/frontend/src/app/(authenticated)/**/*.tsx
shachotwo-app/frontend/src/app/register/page.tsx
shachotwo-app/frontend/src/app/login/page.tsx
shachotwo-app/frontend/src/app/invite/page.tsx
```

### 3. 静的コードチェック

`Agent ui-reviewer: {ファイルパス}` を呼び出してレビュー・修正を実行する。
ファイルが複数ある場合は並列で起動する。

### 4. Playwright視覚チェック（`--visual` 指定時、または全ページ対象時に自動実行）

dev serverが起動していない場合は起動を案内する。

```bash
cd shachotwo-app/frontend

# Playwrightブラウザ未インストールの場合
npx playwright install chromium

# 視覚監査テスト実行
npm run test:visual
```

実行後、以下を確認:
- `e2e/screenshots/` にスクリーンショットが保存される
- `e2e/screenshots/visual-audit-report.json` にJSON形式の違反レポートが出力される
- レポートを読み取り、違反内容をサマリーに含める

視覚チェックで検出する項目（DOM検査ベース）:
- 禁止ワード（BPO, パイプライン, デジタルツイン等）がUI上に表示されていないか
- ボタン・ラベルが英語のみになっていないか
- placeholder が英語のみになっていないか
- ローディングスピナーがテキストなしで表示されていないか
- フォントサイズが最小11px未満になっていないか
- 空状態に次のアクションボタンがあるか

テストが失敗した場合は、スクリーンショットとレポートを元に該当ページのコードを修正する。

### 5. サマリーレポート

全ファイルのレビュー完了後、以下を出力：

```
## UIレビュー完了サマリー

### 静的コードチェック
| ファイル | 修正件数 | 主な違反 |
|---|---|---|
| dashboard/page.tsx | 2件 | 英語placeholder, スピナー裸 |
...

### Playwright視覚チェック（実行した場合）
| ページ | スクリーンショット | 違反数 | 主な違反 |
|---|---|---|---|
| /dashboard | screenshots/dashboard.png | 0件 | — |
...

合計: {N}件の違反を修正しました。
要確認: {N}件（自動修正不可）
```

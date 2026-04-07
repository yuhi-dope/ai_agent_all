---
name: pre-deploy
description: デプロイ前の全体チェック。テスト・CORS・環境変数・マイグレーション・UI_RULES準拠を一括確認する。デプロイ前確認・リリース前チェック・本番反映前の確認時に使用。
argument-hint: ""
allowed-tools: Read, Bash, Grep, Glob
---

# デプロイ前チェック

デプロイ前に全項目を確認します。

## 実行方法

`scripts/run_checks.sh` を**実行**して結果を確認する:

```bash
bash .claude/skills/pre-deploy/scripts/run_checks.sh
```

## チェック項目一覧

| # | チェック項目 | 確認内容 |
|---|---|---|
| 1 | テスト全件パス | pytest 全テスト pass（skip許容、fail NG） |
| 2 | セキュリティ7件 | `/sec-check` の全7項目クリア |
| 3 | 環境変数 | `.env.example` に必須変数定義 |
| 4 | マイグレーション連番 | migrations/ の連番に抜けなし |
| 5 | UI技術用語露出 | model_used/session_id等がUIに露出なし |
| 6 | TypeScript型 | `tsc --noEmit` エラーなし |
| 7 | デバッグコード | console.log/debugger/FIXME の残存なし |

## 結果出力フォーマット

```
## デプロイ前チェック結果 ({日付})

| # | チェック項目 | 状態 | 詳細 |
|---|---|---|---|
| 1 | テスト全件パス | ✅ | XXX passed, X skipped |
| 2 | セキュリティ7件 | ✅ | 全件クリア |
...

総合判定: ✅ デプロイ可能 / ❌ {N}件要対応
```

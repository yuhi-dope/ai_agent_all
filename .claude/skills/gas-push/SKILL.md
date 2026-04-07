---
name: gas-push
description: GASコードをclasp pushでデプロイする。gas/*.jsの変更後に使用。「GASにpush」「デプロイ」「clasp push」等のリクエストにマッチ。
argument-hint: "[optional: specific file or 'open' to open editor]"
allowed-tools: Read, Bash, Glob, Grep
---

# GAS Push — clasp経由でGASにデプロイ

## Step 1: 変更確認

gas/ 配下の変更ファイルを確認:
```bash
cd /Users/sugimotoyuuhi/code/ai_agent/shachotwo-SNSAI/gas && git diff --name-only -- . && git diff --cached --name-only -- .
```

変更がない場合はユーザーに伝えて終了。

## Step 2: 構文チェック

gas/*.js に明らかな構文エラーがないか簡易確認:
```bash
cd /Users/sugimotoyuuhi/code/ai_agent/shachotwo-SNSAI/gas && for f in *.js; do node --check "$f" 2>&1 || echo "SYNTAX ERROR: $f"; done
```

エラーがあれば修正を提案する（自動修正はしない）。

## Step 3: Push

```bash
cd /Users/sugimotoyuuhi/code/ai_agent/shachotwo-SNSAI/gas && clasp push --force
```

## Step 4: 結果報告

push結果を表示。引数に `open` が含まれていれば:
```bash
cd /Users/sugimotoyuuhi/code/ai_agent/shachotwo-SNSAI/gas && clasp open
```

## GASプロジェクト情報

- スクリプトID: `.clasp.json` の `scriptId` を参照
- エディタURL: `clasp open` で開く
- スプレッドシートID: GASのスクリプトプロパティ `SPREADSHEET_ID`

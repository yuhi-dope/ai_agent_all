---
name: hotfix
description: 緊急バグ修正の高速フロー。修正→テスト→差分確認を最小ステップで実行する。「バグ修正」「緊急修正」「hotfix」「すぐ直して」等のリクエストにマッチ。
argument-hint: "[bug-description or file-path]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Hotfix — 緊急修正フロー

対象: $ARGUMENTS

## Step 1: 原因特定

- エラーログ・スタックトレースから原因ファイルを特定
- 最小限の修正範囲を判断

## Step 2: 修正

- 原因ファイルを直接修正（サブエージェントに委譲しない）
- 修正は最小限に。リファクタリングはしない

## Step 3: テスト

```bash
cd shachotwo-app && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

- `-x` で最初の失敗で止める（高速化）
- 修正対象の関連テストが pass であること

## Step 4: 差分確認

```bash
git diff --stat
git diff
```

- 意図しない変更がないか確認
- 修正が最小限であることを確認

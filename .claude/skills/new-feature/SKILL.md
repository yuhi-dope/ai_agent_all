---
name: new-feature
description: 新機能の実装フロー。設計確認→実装→テスト→レビューを一気通貫で実行する。「新機能を作りたい」「機能追加したい」「新しく○○を作る」等のリクエストにマッチ。
argument-hint: "[module-path] [feature-description]"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

# New Feature — 実装一気通貫フロー

対象: $ARGUMENTS

## Step 1: 設計確認

`/design-index` を参照し、対象モジュールに関連する設計書を特定して読む。

## Step 2: 実装

`/implement $ARGUMENTS` を実行。パスに応じて適切なサブエージェントに振り分け。

## Step 3: テスト

`/test-module $ARGUMENTS` を実行。失敗があれば修正。

## Step 4: レビュー（対象に応じて）

- **frontend/** の場合: `/ui-review` を実行
- **workers/bpo/** の場合: `/run-pipeline` で精度検証
- **routers/** の場合: API仕様の整合性確認

## Step 5: 設計書同期

`/sync-design $ARGUMENTS` を実行。コード変更に連動して関連設計ドキュメントを自動更新。

## 完了条件

- テスト全件 pass
- レビュー指摘 0件
- 型チェック エラーなし（フックで自動確認済み）
- 設計書がコードと整合（/sync-design で確認済み）

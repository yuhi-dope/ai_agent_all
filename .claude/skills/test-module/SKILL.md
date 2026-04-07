---
name: test-module
description: 指定モジュールのテストを実行し、失敗があれば修正する。テスト実行は親で直接行い、完全な出力を確認してから修正判断する。
argument-hint: "[module-path] (例: brain, brain/extraction, routers, workers/bpo/construction)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

## テスト実行 & 修正

対象: $ARGUMENTS

### ★重要: テスト実行はサブエージェントに委譲しない

テスト失敗時のデバッグには完全なスタックトレース・アサーションエラーの全文が必要。
サブエージェントに委譲すると結果が要約され、情報が失われる。

### Step 1: 親で直接テスト実行（委譲しない）

```bash
cd shachotwo-app
python -m pytest tests/$ARGUMENTS -v 2>&1 | head -200
```

- 引数が空の場合は `tests/` 全体を実行
- 完全な出力を親のコンテキストで確認する
- 失敗がなければここで完了

### Step 2: 失敗分析（親で実行）

失敗があれば、親（あなた自身）が以下を判断する:
- スタックトレースを読み、根本原因を特定
- 修正すべきファイル（テストコード or プロダクトコード）を判断

### Step 3: 修正の実行

修正内容に応じて分岐:

**A. 単純な修正（1-3ファイル）→ 親で直接修正**
- import修正、モック不足、型エラーなど
- 修正後、再度 `python -m pytest` で確認

**B. 大規模な修正（4ファイル以上）→ test-runnerに委譲**
- `Agent test-runner` に失敗内容と修正方針を具体的に指示
- 指示には失敗したテスト名・スタックトレースの要点を含める

### テスト規約

- 外部API（LLM, Supabase, Voyage AI）は全てモック
- `unittest.mock.patch` + `AsyncMock` を使用
- テストデータは日本語
- `tests/` 配下に `__init__.py` は作成しない（名前空間衝突防止）

---
name: code-check
description: コードエラーチェック。Python構文エラー・型エラー・未使用import・TypeScript型エラー・空ファイル・循環importを一括確認する。実装後・PR前・エラー調査時に使用。
argument-hint: "[対象ディレクトリ or ファイル（省略時は shachotwo-app/ 全体）]"
allowed-tools: Read, Bash, Grep, Glob
---

# コードエラーチェック

`$ARGUMENTS` が指定された場合はその対象のみ。省略時は `shachotwo-app/` 全体。

## 実行

```bash
bash .claude/skills/code-check/scripts/run_checks.sh
```

## チェック項目

| # | チェック項目 | ツール | 確認内容 |
|---|---|---|---|
| 1 | Python構文エラー | `python -m py_compile` | SyntaxError が発生するファイル |
| 2 | 未使用import | `ruff check --select F401` | importされているが使われていない |
| 3 | 未定義変数 | `ruff check --select F821` | 定義前に使用されている変数 |
| 4 | 循環import | import スキャン | A→B→A の依存ループ |
| 5 | TypeScript型エラー | `tsc --noEmit` | 型不一致・未定義型 |
| 6 | 空ファイル（0バイト） | `find -empty` | 誤って作成された空ファイル |
| 7 | .envの機密情報露出 | `grep` | コードに直書きされたAPIキー・パスワード |

## 結果フォーマット

```
## コードエラーチェック結果 ({日付})

| # | チェック項目 | 状態 | 詳細 |
|---|---|---|---|
| 1 | Python構文エラー | ✅ | エラーなし |
| 2 | 未使用import | ⚠️ | 3件（自動修正可） |
...

総合判定: ✅ 問題なし / ❌ {N}件要対応
```

問題が見つかった場合は修正案を提示し、自動修正可能なものは実行する。

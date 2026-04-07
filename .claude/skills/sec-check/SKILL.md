---
name: sec-check
description: 導入前セキュリティチェック7件を自動確認する。パイロット企業投入前・デプロイ前に実行する。セキュリティチェック・脆弱性確認・導入前確認時に使用。
argument-hint: ""
allowed-tools: Read, Bash, Grep, Glob
---

# セキュリティチェック

導入前必須対応7件を自動確認します。

## 実行方法

`scripts/run_checks.sh` を**実行**して結果を確認する:

```bash
bash .claude/skills/sec-check/scripts/run_checks.sh
```

## チェック項目一覧

| # | 項目 | 確認内容 |
|---|---|---|
| 1 | エラーメッセージ安全化 | `install_error_handlers` 適用 + `detail=str(e)` 残存なし |
| 2 | レート制限 | 主要エンドポイントに `check_rate_limit` 適用 |
| 3 | LLMコスト上限 | `CostTracker` + `check_budget` 実装 |
| 4 | タイムアウト設定 | LLM呼び出しに `timeout` / `wait_for` 設定 |
| 5 | 同時実行制御 | テナント別 `Semaphore` 実装 |
| 6 | マルチテナント分離テスト | `test_tenant_isolation.py` 存在 + pass |
| 7 | CORS制限 | 本番環境でCORS制限 |

## 結果出力フォーマット

```
## セキュリティチェック結果 ({日付})

| # | 項目 | 状態 | 詳細 |
|---|---|---|---|
| 1 | エラーメッセージ安全化 | ✅ | install_error_handlers 適用済み |
| 2 | レート制限 | ✅ | 全エンドポイント適用済み |
...

総合判定: ✅ 全件クリア / ❌ {N}件未対応（要修正）
```

未対応項目があれば修正方法を提示する。

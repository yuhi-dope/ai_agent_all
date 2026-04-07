#!/bin/bash
# sec-check: セキュリティ7項目自動チェック
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
APP_DIR="$REPO_ROOT/shachotwo-app"

pass=0; fail=0; total=7

check() {
  local num="$1" name="$2" result="$3"
  if [ "$result" = "ok" ]; then
    echo "✅ #$num $name"
    pass=$((pass + 1))
  else
    echo "❌ #$num $name — $result"
    fail=$((fail + 1))
  fi
}

# #1 エラーメッセージ安全化
if grep -q "install_error_handlers\|error_handler" "$APP_DIR/main.py" 2>/dev/null; then
  leaked=$(grep -rn "detail=str(e)" "$APP_DIR/routers/" --include="*.py" 2>/dev/null | head -5)
  if [ -z "$leaked" ]; then
    check 1 "エラーメッセージ安全化" "ok"
  else
    check 1 "エラーメッセージ安全化" "detail=str(e)が残存"
  fi
else
  check 1 "エラーメッセージ安全化" "install_error_handlersが未適用"
fi

# #2 レート制限
rate_count=$(grep -rl "check_rate_limit" "$APP_DIR/routers/" --include="*.py" 2>/dev/null | wc -l | tr -d ' ')
if [ "$rate_count" -ge 5 ]; then
  check 2 "レート制限" "ok"
else
  check 2 "レート制限" "${rate_count}ファイルのみ適用"
fi

# #3 LLMコスト上限
if grep -q "check_budget\|record_cost\|CostTracker" "$APP_DIR/llm/cost_tracker.py" 2>/dev/null; then
  check 3 "LLMコスト上限" "ok"
else
  check 3 "LLMコスト上限" "CostTracker未実装"
fi

# #4 タイムアウト設定
if grep -q "wait_for\|timeout" "$APP_DIR/llm/client.py" 2>/dev/null; then
  check 4 "タイムアウト設定" "ok"
else
  check 4 "タイムアウト設定" "timeout未設定"
fi

# #5 同時実行制御
if grep -q "Semaphore\|semaphore" "$APP_DIR/workers/bpo/manager/task_router.py" 2>/dev/null; then
  check 5 "同時実行制御" "ok"
else
  check 5 "同時実行制御" "Semaphore未実装"
fi

# #6 マルチテナント分離テスト
if [ -f "$APP_DIR/tests/security/test_tenant_isolation.py" ]; then
  check 6 "マルチテナント分離テスト" "ok"
else
  check 6 "マルチテナント分離テスト" "テストファイル未作成"
fi

# #7 CORS制限
if grep -q "CORS\|allow_origins" "$APP_DIR/main.py" 2>/dev/null; then
  check 7 "CORS制限" "ok"
else
  check 7 "CORS制限" "CORS設定なし"
fi

echo ""
echo "--- 結果: ${pass}/${total} クリア ---"
if [ "$fail" -gt 0 ]; then
  echo "❌ ${fail}件未対応"
  exit 1
else
  echo "✅ 全件クリア"
fi

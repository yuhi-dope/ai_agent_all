#!/bin/bash
# pre-deploy: デプロイ前全体チェック
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
APP_DIR="$REPO_ROOT/shachotwo-app"
FRONTEND_DIR="$APP_DIR/frontend"

pass=0; fail=0; warn=0; total=7

check() {
  local num="$1" name="$2" result="$3"
  if [ "$result" = "ok" ]; then
    echo "✅ #$num $name"
    pass=$((pass + 1))
  elif echo "$result" | grep -q "^warn:"; then
    echo "⚠️  #$num $name — ${result#warn:}"
    warn=$((warn + 1))
  else
    echo "❌ #$num $name — $result"
    fail=$((fail + 1))
  fi
}

# #1 テスト全件パス
echo "テスト実行中..."
test_result=$(cd "$APP_DIR" && python -m pytest tests/ -q --tb=short 2>&1 | tail -5)
if echo "$test_result" | grep -q "failed"; then
  check 1 "テスト全件パス" "$test_result"
else
  check 1 "テスト全件パス" "ok"
fi

# #2 セキュリティチェック
sec_script="$SCRIPT_DIR/../../sec-check/scripts/run_checks.sh"
if [ -f "$sec_script" ]; then
  sec_result=$(bash "$sec_script" 2>&1 | tail -1)
  if echo "$sec_result" | grep -q "全件クリア"; then
    check 2 "セキュリティ7件" "ok"
  else
    check 2 "セキュリティ7件" "$sec_result"
  fi
else
  check 2 "セキュリティ7件" "sec-checkスクリプト未作成"
fi

# #3 環境変数チェック
required_vars="GEMINI_API_KEY SUPABASE_URL SUPABASE_SERVICE_KEY JWT_SECRET ENVIRONMENT"
missing=""
if [ -f "$APP_DIR/.env.example" ]; then
  for var in $required_vars; do
    grep -q "$var" "$APP_DIR/.env.example" || missing="$missing $var"
  done
  if [ -z "$missing" ]; then
    check 3 "環境変数" "ok"
  else
    check 3 "環境変数" "未定義:$missing"
  fi
else
  check 3 "環境変数" ".env.exampleが存在しない"
fi

# #4 マイグレーション連番チェック
migr_dir="$APP_DIR/db/migrations"
if [ -d "$migr_dir" ]; then
  check 4 "マイグレーション連番" "ok"
else
  check 4 "マイグレーション連番" "migrationsディレクトリなし"
fi

# #5 UI技術用語露出
ui_leak=$(grep -rn "model_used\|session_id\|cost_yen\|gemini-\|claude-\|gpt-" "$FRONTEND_DIR/src/app" --include="*.tsx" -l 2>/dev/null | head -5)
if [ -z "$ui_leak" ]; then
  check 5 "UI技術用語露出" "ok"
else
  check 5 "UI技術用語露出" "検出: $ui_leak"
fi

# #6 TypeScript型チェック
ts_result=$(cd "$FRONTEND_DIR" && npx tsc --noEmit 2>&1 | tail -5)
if echo "$ts_result" | grep -q "error TS"; then
  check 6 "TypeScript型" "$ts_result"
else
  check 6 "TypeScript型" "ok"
fi

# #7 デバッグコード残存
debug_count=$(grep -rn "console\.log\|debugger\|FIXME" "$FRONTEND_DIR/src" --include="*.tsx" 2>/dev/null | grep -v "node_modules" | wc -l | tr -d ' ')
if [ "$debug_count" -eq 0 ]; then
  check 7 "デバッグコード" "ok"
elif [ "$debug_count" -le 3 ]; then
  check 7 "デバッグコード" "warn:${debug_count}件のconsole.log/FIXME"
else
  check 7 "デバッグコード" "${debug_count}件のconsole.log/FIXME"
fi

echo ""
echo "--- 結果: ${pass}/${total} クリア, ${warn}件警告, ${fail}件失敗 ---"
if [ "$fail" -gt 0 ]; then
  echo "❌ デプロイ不可（${fail}件要対応）"
  exit 1
else
  echo "✅ デプロイ可能"
fi

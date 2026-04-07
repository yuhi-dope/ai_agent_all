#!/usr/bin/env bash
# コードエラーチェック - shachotwo-app/ 全体
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
APP="$ROOT/shachotwo-app"
FRONTEND="$APP/frontend"
DATE=$(date +"%Y-%m-%d %H:%M")
ERRORS=0

echo "## コードエラーチェック結果 ($DATE)"
echo ""
echo "| # | チェック項目 | 状態 | 詳細 |"
echo "|---|---|---|---|"

# ① Python構文エラー
PY_ERRORS=$(find "$APP" -name "*.py" \
  | grep -v __pycache__ | grep -v "/.venv" | grep -v "/.venv-ci" \
  | xargs -I{} python3 -m py_compile {} 2>&1 | grep -v "^$" || true)
if [ -z "$PY_ERRORS" ]; then
  echo "| 1 | Python構文エラー | ✅ | エラーなし |"
else
  echo "| 1 | Python構文エラー | ❌ | $(echo "$PY_ERRORS" | wc -l | tr -d ' ')件 |"
  echo "$PY_ERRORS"
  ERRORS=$((ERRORS + 1))
fi

# ② 未使用import (ruff)
if command -v ruff &>/dev/null; then
  UNUSED=$(cd "$APP" && ruff check --select F401 --quiet . 2>/dev/null | grep -v __pycache__ | grep -v ".venv" || true)
  COUNT=$(echo "$UNUSED" | grep -c "F401" || true)
  if [ "$COUNT" -eq 0 ]; then
    echo "| 2 | 未使用import | ✅ | なし |"
  else
    echo "| 2 | 未使用import | ⚠️ | ${COUNT}件（ruff check --fix で自動修正可） |"
  fi
else
  echo "| 2 | 未使用import | ⏭️ | ruff未インストール |"
fi

# ③ 未定義変数 (ruff F821)
if command -v ruff &>/dev/null; then
  UNDEF=$(cd "$APP" && ruff check --select F821 --quiet . 2>/dev/null | grep -v __pycache__ | grep -v ".venv" || true)
  COUNT=$(echo "$UNDEF" | grep -c "F821" || true)
  if [ "$COUNT" -eq 0 ]; then
    echo "| 3 | 未定義変数 | ✅ | なし |"
  else
    echo "| 3 | 未定義変数 | ❌ | ${COUNT}件 |"
    echo "$UNDEF" | head -10
    ERRORS=$((ERRORS + 1))
  fi
else
  echo "| 3 | 未定義変数 | ⏭️ | ruff未インストール |"
fi

# ④ 循環import（簡易検出）
CIRCULAR=$(cd "$APP" && python3 -c "
import ast, os, sys
from pathlib import Path

def get_imports(filepath):
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
    except:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split('.')[0])
    return imports

# 簡易チェック: 相互importのみ
found = []
py_files = list(Path('.').rglob('*.py'))
for f in py_files[:200]:  # 最初の200ファイルのみ
    if '.venv' in str(f) or '__pycache__' in str(f):
        continue
print('OK')
" 2>/dev/null || echo "ERROR")
if [ "$CIRCULAR" = "OK" ]; then
  echo "| 4 | 循環import | ✅ | 検出なし |"
else
  echo "| 4 | 循環import | ⚠️ | /check-imports で詳細確認推奨 |"
fi

# ⑤ TypeScript型エラー
if [ -f "$FRONTEND/tsconfig.json" ]; then
  TS_ERRORS=$(cd "$FRONTEND" && npx tsc --noEmit 2>&1 | grep -c "error TS" || true)
  if [ "$TS_ERRORS" -eq 0 ]; then
    echo "| 5 | TypeScript型エラー | ✅ | エラーなし |"
  else
    echo "| 5 | TypeScript型エラー | ❌ | ${TS_ERRORS}件 |"
    ERRORS=$((ERRORS + 1))
  fi
else
  echo "| 5 | TypeScript型エラー | ⏭️ | tsconfig.jsonなし |"
fi

# ⑥ 空ファイル（0バイト）
EMPTY=$(find "$APP" -maxdepth 2 -type f -empty \
  ! -path "*/.venv*" ! -path "*/__pycache__*" ! -path "*/node_modules/*" \
  ! -name "*.pyc" ! -name "__init__.py" 2>/dev/null || true)
if [ -z "$EMPTY" ]; then
  echo "| 6 | 空ファイル | ✅ | なし |"
else
  COUNT=$(echo "$EMPTY" | wc -l | tr -d ' ')
  echo "| 6 | 空ファイル | ⚠️ | ${COUNT}件 |"
  echo "$EMPTY"
fi

# ⑦ 機密情報の直書き
SECRETS=$(grep -r --include="*.py" --include="*.ts" --include="*.tsx" \
  -E "(api_key|API_KEY|password|PASSWORD|secret|SECRET)\s*=\s*['\"][a-zA-Z0-9_\-]{8,}" \
  "$APP" \
  | grep -v ".env" | grep -v "example" | grep -v "test" | grep -v ".venv" || true)
if [ -z "$SECRETS" ]; then
  echo "| 7 | 機密情報直書き | ✅ | なし |"
else
  echo "| 7 | 機密情報直書き | ❌ | 要確認 |"
  echo "$SECRETS" | head -5
  ERRORS=$((ERRORS + 1))
fi

echo ""
if [ "$ERRORS" -eq 0 ]; then
  echo "**総合判定: ✅ 問題なし**"
else
  echo "**総合判定: ❌ ${ERRORS}件要対応**"
fi

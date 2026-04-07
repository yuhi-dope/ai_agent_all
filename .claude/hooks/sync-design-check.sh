#!/bin/bash
# 設計書自動同期リマインダー
# workers/, routers/, db/migrations/ の変更を検知してリマインドする
FILE=$(jq -r '.tool_input.file_path // .tool_input.filePath // empty')
if [ -z "$FILE" ]; then exit 0; fi

echo "$FILE" | grep -qE 'shachotwo-app/(workers|routers|db/migrations)/.*\.(py|sql)$' || exit 0

BASENAME=$(basename "$FILE")
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"[設計書同期] ${BASENAME} が変更されました。パイプライン/ルーター/DB変更時は設計書(d_07/d_08/d_06/DESIGN_INDEX)の整合性を確認し、必要なら /sync-design を実行してください。"}}
EOF

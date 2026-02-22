#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
docker build -t ai-agent-sandbox:latest "$SCRIPT_DIR"
echo "Built ai-agent-sandbox:latest"

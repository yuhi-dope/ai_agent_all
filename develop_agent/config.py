"""develop_agent 専用の定数・設定。guardrails と file_filter から参照する。"""

# 共通定数は agent.config から re-export
from agent.config import GENRES, GENRE_CHOICES, STEP_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS  # noqa: F401

# ループ・リトライ
MAX_RETRY = 3

# ファイル読込制限（Coder Agent のコンテキスト用）
FILE_SIZE_LIMIT_BYTES = 20 * 1024  # 20KB
EXCLUDED_FILE_PATTERNS = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "*.log",
    ".env",
    ".env.*",
    "node_modules",
    "__pycache__",
    "*.pyc",
    ".git",
]

# push 変更量の目安
MAX_LINES_PER_PUSH = 200

# 段階的テストのタイムアウト（秒）
UNIT_TEST_TIMEOUT_SECONDS = 120
E2E_TEST_TIMEOUT_SECONDS = 300

# ── Sandbox (Docker) ───────────────────────────────────
SANDBOX_IMAGE = "ai-agent-sandbox:latest"
SANDBOX_MEMORY_LIMIT = "512m"
SANDBOX_CPU_LIMIT = "1.0"
SANDBOX_PIDS_LIMIT = 256
SANDBOX_NETWORK = "none"

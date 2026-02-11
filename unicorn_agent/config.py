"""定数・設定。guardrails と file_filter から参照する。"""

# ループ・リトライ
MAX_RETRY = 3
STEP_TIMEOUT_SECONDS = 180
TOTAL_TIMEOUT_SECONDS = 600

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

# PR 変更量の目安（develop_agent より）
MAX_LINES_PER_PR = 200

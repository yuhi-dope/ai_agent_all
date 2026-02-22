"""定数・設定。guardrails と file_filter から参照する。"""

# 専門家ジャンル（Notion の専門家ジャンル選択肢・ルール紐付け用）
GENRES = [
    "事務",
    "法務",
    "会計",
    "情シス",
    "SFA",
    "CRM",
    "ブレイン",
    "M&A・DD",
]
GENRE_CHOICES = tuple(GENRES)

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

# 段階的テストのタイムアウト（秒）
UNIT_TEST_TIMEOUT_SECONDS = 120
E2E_TEST_TIMEOUT_SECONDS = 300

# ── Sandbox (Docker) ───────────────────────────────────
SANDBOX_IMAGE = "ai-agent-sandbox:latest"
SANDBOX_MEMORY_LIMIT = "512m"
SANDBOX_CPU_LIMIT = "1.0"
SANDBOX_PIDS_LIMIT = 256
SANDBOX_NETWORK = "none"

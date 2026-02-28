"""共通定数・設定。develop_agent / bpo_agent 両方から参照する。"""

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

# タイムアウト
STEP_TIMEOUT_SECONDS = 300
TOTAL_TIMEOUT_SECONDS = 600

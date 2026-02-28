"""bpo_agent 専用の定数・設定。"""

# BPO タスク計画の最大操作数
MAX_OPERATIONS_PER_TASK = 10

# 失敗学習のしきい値（同じパターンが N 回蓄積でルール候補生成）
RULE_GENERATION_THRESHOLD = 3

"""bpo_agent 専用の定数・設定。"""

# 共通定数は agent.config から re-export
from agent.config import STEP_TIMEOUT_SECONDS, TOTAL_TIMEOUT_SECONDS  # noqa: F401

# BPO タスク計画の最大操作数
MAX_OPERATIONS_PER_TASK = 10

# 失敗学習のしきい値（同じパターンが N 回蓄積でルール候補生成）
RULE_GENERATION_THRESHOLD = 3

"""共通マイクロエージェントモデル定義。"""
from pydantic import BaseModel
from typing import Any, Optional


class MicroAgentInput(BaseModel):
    company_id: str
    agent_name: str
    payload: dict[str, Any]
    context: dict[str, Any] = {}  # 前ステップからの引き継ぎデータ


class MicroAgentOutput(BaseModel):
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float  # 0.0〜1.0
    cost_yen: float
    duration_ms: int
    log_id: Optional[str] = None


class MicroAgentError(Exception):
    def __init__(self, agent_name: str, step: str, message: str):
        self.agent_name = agent_name
        self.step = step
        super().__init__(f"[{agent_name}/{step}] {message}")

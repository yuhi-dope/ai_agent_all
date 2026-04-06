"""BPO Manager 共通モデル定義。"""
from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime

# 汎用Enumは shared.enums に一元化。後方互換のため再エクスポート。
from shared.enums import TriggerType, ExecutionLevel  # noqa: F401


class BPOTask(BaseModel):
    id: Optional[str] = None
    company_id: str
    pipeline: str                          # PIPELINE_REGISTRYのキー
    trigger_type: TriggerType
    execution_level: ExecutionLevel = ExecutionLevel.DRAFT_CREATE
    input_data: dict[str, Any] = Field(default_factory=dict)
    estimated_impact: float = 0.5          # 0〜1（影響度）
    requires_approval: bool = True
    knowledge_item_ids: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    context: dict[str, Any] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    success: bool
    pipeline: str
    steps: list[dict] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: Optional[str] = None
    approval_pending: bool = False         # 承認待ち状態かどうか

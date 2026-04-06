"""Pydanticモデル — デジタルツイン5次元状態。"""
from pydantic import BaseModel, Field


class PeopleState(BaseModel):
    """人材・組織次元の状態。"""

    headcount: int = 0
    departments: list[str] = Field(default_factory=list)
    key_roles: list[str] = Field(default_factory=list)
    skill_gaps: list[str] = Field(default_factory=list)
    completeness: float = 0.0  # 0.0-1.0


class ProcessState(BaseModel):
    """業務フロー・ルール・意思決定次元の状態。"""

    documented_flows: int = 0
    decision_rules: int = 0
    automation_rate: float = 0.0  # 自動化率 0.0-1.0
    bottlenecks: list[str] = Field(default_factory=list)
    completeness: float = 0.0


class CostState(BaseModel):
    """コスト構造・原価・予算次元の状態。"""

    monthly_fixed_cost: int = 0
    monthly_variable_cost: int = 0
    top_cost_items: list[str] = Field(default_factory=list)
    cost_reduction_opportunities: list[str] = Field(default_factory=list)
    completeness: float = 0.0


class ToolState(BaseModel):
    """SaaS・システム・ツール次元の状態。"""

    saas_tools: list[str] = Field(default_factory=list)
    connected_tools: list[str] = Field(default_factory=list)   # connector経由で接続済み
    manual_tools: list[str] = Field(default_factory=list)      # まだ手動
    automation_opportunities: list[str] = Field(default_factory=list)
    completeness: float = 0.0


class RiskState(BaseModel):
    """リスク・コンプライアンス次元の状態。"""

    open_risks: list[str] = Field(default_factory=list)
    compliance_items: list[str] = Field(default_factory=list)
    severity_high: int = 0
    severity_medium: int = 0
    completeness: float = 0.0


class TwinSnapshot(BaseModel):
    """会社のデジタルツイン全体スナップショット。"""

    company_id: str
    people: PeopleState = Field(default_factory=PeopleState)
    process: ProcessState = Field(default_factory=ProcessState)
    cost: CostState = Field(default_factory=CostState)
    tool: ToolState = Field(default_factory=ToolState)
    risk: RiskState = Field(default_factory=RiskState)
    overall_completeness: float = 0.0

    def recalculate_overall_completeness(self) -> None:
        """5次元の completeness 平均で overall_completeness を再計算する。"""
        scores = [
            self.people.completeness,
            self.process.completeness,
            self.cost.completeness,
            self.tool.completeness,
            self.risk.completeness,
        ]
        self.overall_completeness = round(sum(scores) / len(scores), 4)

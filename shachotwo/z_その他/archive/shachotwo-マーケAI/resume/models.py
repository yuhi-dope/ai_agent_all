"""職務経歴書 Pydanticモデル"""

from __future__ import annotations

from pydantic import BaseModel


class Benefit(BaseModel):
    icon: str
    text: str


class ResumeData(BaseModel):
    company_name: str
    contact_name: str = ""
    industry: str
    monthly_price: str = "30,000"
    strong_tasks: list[str]  # すぐ対応可能な業務
    learnable_tasks: list[str]  # 学習後に対応可能な業務
    benefits: list[Benefit]  # メリット試算
    appeal_message: str  # 訴求メッセージ
    scale_tone: str  # 零細/小規模/中規模


class IndustryTemplate(BaseModel):
    code: str  # construction / manufacturing / dental / care / professional / realestate
    name: str  # 建設業 / 製造業 / ...
    tasks: list[str]  # 全業務リスト（8つ）
    appeal_template: str  # 訴求テンプレート

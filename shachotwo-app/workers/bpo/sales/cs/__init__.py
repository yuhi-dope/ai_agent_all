"""workers/bpo/sales/cs — CS（カスタマーサクセス）AI社員。

パイプライン:
    support_auto_response_pipeline  — CS⑥  サポート自動対応
    upsell_briefing_pipeline        — CS⑦  アップセル支援（コンサルへのブリーフィング）
    cancellation_pipeline           — CRM⑩ 解約フロー（データエクスポート/最終請求/テナント無効化/学習）
"""
from workers.bpo.sales.cs.support_auto_response_pipeline import (
    run_support_auto_response_pipeline,
    SupportAutoResponseResult,
)
from workers.bpo.sales.cs.upsell_briefing_pipeline import (
    run_upsell_briefing_pipeline,
    UpsellBriefingPipelineResult,
    UpsellOpportunity,
)
from workers.bpo.sales.cs.cancellation_pipeline import (
    run_cancellation_pipeline,
    CancellationPipelineResult,
    StepResult as CancellationStepResult,
)

__all__ = [
    "run_support_auto_response_pipeline",
    "SupportAutoResponseResult",
    "run_upsell_briefing_pipeline",
    "UpsellBriefingPipelineResult",
    "UpsellOpportunity",
    "run_cancellation_pipeline",
    "CancellationPipelineResult",
    "CancellationStepResult",
]

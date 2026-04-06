"""workers/bpo/sales/crm — CRM（顧客関係管理）AI社員。

パイプライン:
    customer_lifecycle_pipeline  — CRM④  顧客ライフサイクル管理（オンボーディング + ヘルススコア日次）
    revenue_request_pipeline     — CRM⑤  売上・要望管理（MRR/ARR算出 + 要望管理ボード）
"""
from workers.bpo.sales.crm.customer_lifecycle_pipeline import (
    run_customer_lifecycle_pipeline,
    CustomerLifecyclePipelineResult,
)
from workers.bpo.sales.crm.revenue_request_pipeline import (
    run_revenue_request_pipeline,
    RevenueRequestPipelineResult,
    StepResult as RevenueRequestStepResult,
)

__all__ = [
    "run_customer_lifecycle_pipeline",
    "CustomerLifecyclePipelineResult",
    "run_revenue_request_pipeline",
    "RevenueRequestPipelineResult",
    "RevenueRequestStepResult",
]

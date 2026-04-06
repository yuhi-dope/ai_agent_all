"""workers/bpo/sales/marketing — マーケティングAI社員。

パイプライン:
    outreach_pipeline  — マーケ⓪ 企業リサーチ＆アウトリーチ（400件/日自動）
"""
from workers.bpo.sales.marketing.outreach_pipeline import (
    run_outreach_pipeline,
    OutreachPipelineResult,
    OutreachRecord,
)

__all__ = [
    "run_outreach_pipeline",
    "OutreachPipelineResult",
    "OutreachRecord",
]
